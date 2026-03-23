"""Lambda function to reset all fault handlers to their original faulty code.

Reads fault_api.txt, fault_db.txt, fault_sql.txt from GitHub and pushes
their content as views_api.py, views_db.py, views_sql.py respectively.
The push to main triggers the existing CI/CD pipeline for ECS deployment.
"""

import base64
import json
import os
import urllib.request

import boto3

GITHUB_API = "https://api.github.com"

# Maps source fault template files to their target views files
FAULT_RESET_MAP = {
    "hello/page/fault_sql.txt": "hello/page/views_sql.py",
    "hello/page/fault_api.txt": "hello/page/views_api.py",
    "hello/page/fault_db.txt": "hello/page/views_db.py",
}


def get_token():
    arn = os.environ["GITHUB_SECRET_ARN"]
    secrets = boto3.client("secretsmanager")
    sec = secrets.get_secret_value(SecretId=arn)["SecretString"]
    return json.loads(sec)["GITHUB_TOKEN"]


def gh_request(method: str, path: str, token: str, body=None):
    url = f"{GITHUB_API}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "reset-lambda",
        },
    )

    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def create_atomic_reset_commit(
    owner: str,
    repo: str,
    branch: str,
    token: str,
    file_updates: dict[str, str],
) -> str:
    """Create one Git commit that restores every changed fault file together."""
    ref = gh_request(
        "GET",
        f"/repos/{owner}/{repo}/git/ref/heads/{branch}",
        token,
    )
    base_commit_sha = ref["object"]["sha"]
    base_commit = gh_request(
        "GET",
        f"/repos/{owner}/{repo}/git/commits/{base_commit_sha}",
        token,
    )
    base_tree_sha = base_commit["tree"]["sha"]

    tree_entries = []
    for target_path, content in file_updates.items():
        blob = gh_request(
            "POST",
            f"/repos/{owner}/{repo}/git/blobs",
            token,
            {
                "content": content,
                "encoding": "utf-8",
            },
        )
        tree_entries.append({
            "path": target_path,
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"],
        })

    new_tree = gh_request(
        "POST",
        f"/repos/{owner}/{repo}/git/trees",
        token,
        {
            "base_tree": base_tree_sha,
            "tree": tree_entries,
        },
    )
    new_commit = gh_request(
        "POST",
        f"/repos/{owner}/{repo}/git/commits",
        token,
        {
            "message": "[RESET] Restore all faulty handlers for self-healing loop testing",
            "tree": new_tree["sha"],
            "parents": [base_commit_sha],
        },
    )
    gh_request(
        "PATCH",
        f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
        token,
        {
            "sha": new_commit["sha"],
            "force": False,
        },
    )
    return new_commit["sha"]


def lambda_handler(event, context):
    print(json.dumps(event))

    owner = os.environ["GITHUB_OWNER"]
    repo = os.environ["GITHUB_REPO"]
    branch = "main"
    token = get_token()

    results = {}
    any_committed = False
    commit_sha = None
    pending_writes = {}
    top_level_error = None

    for source_path, target_path in FAULT_RESET_MAP.items():
        try:
            # Read the fault template content from GitHub
            source_file = gh_request(
                "GET",
                f"/repos/{owner}/{repo}/contents/{source_path}?ref={branch}",
                token,
            )
            fault_content = base64.b64decode(source_file["content"]).decode("utf-8")

            # Read the current target file to get its SHA
            target_file = gh_request(
                "GET",
                f"/repos/{owner}/{repo}/contents/{target_path}?ref={branch}",
                token,
            )
            target_sha = target_file["sha"]
            existing_content = base64.b64decode(target_file["content"]).decode("utf-8")

            # Skip if content already matches
            if existing_content.rstrip("\n") == fault_content.rstrip("\n"):
                results[target_path] = {
                    "ok": True,
                    "no_change": True,
                    "source": source_path,
                }
                continue

            results[target_path] = {
                "ok": True,
                "no_change": False,
                "source": source_path,
            }
            pending_writes[target_path] = {
                "source": source_path,
                "content": fault_content,
                "target_sha": target_sha,
            }

        except Exception as e:
            results[target_path] = {
                "ok": False,
                "source": source_path,
                "error": str(e),
            }

    if pending_writes and all(r.get("ok", False) for r in results.values()):
        try:
            commit_sha = create_atomic_reset_commit(
                owner=owner,
                repo=repo,
                branch=branch,
                token=token,
                file_updates={
                    target_path: info["content"]
                    for target_path, info in pending_writes.items()
                },
            )
            any_committed = True
            for target_path, info in pending_writes.items():
                results[target_path] = {
                    "ok": True,
                    "no_change": False,
                    "source": info["source"],
                    "commit_sha": commit_sha,
                }
        except Exception as e:
            top_level_error = str(e)
            for target_path, info in pending_writes.items():
                results[target_path] = {
                    "ok": False,
                    "no_change": False,
                    "source": info["source"],
                    "error": top_level_error,
                }

    # If no files changed, force ECS redeployment so the service
    # picks up whatever is already in the latest image.
    forced_ecs_deploy = False
    if not any_committed and not pending_writes and all(r.get("ok", False) for r in results.values()):
        try:
            ecs = boto3.client("ecs")
            ecs.update_service(
                cluster=os.environ.get("ECS_CLUSTER", "creamandonion"),
                service=os.environ.get("ECS_SERVICE", "cream-task-service"),
                forceNewDeployment=True,
            )
            forced_ecs_deploy = True
        except Exception as e:
            print(f"Failed to force ECS redeployment: {e}")

    return {
        "success": all(r.get("ok", False) for r in results.values()) and (
            not pending_writes or any_committed
        ),
        "results": results,
        "any_committed": any_committed,
        "forced_ecs_deploy": forced_ecs_deploy,
        "commit_sha": commit_sha,
        "error": top_level_error,
    }
