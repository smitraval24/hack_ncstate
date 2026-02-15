import base64
import json
import os
import urllib.request
import boto3

secrets = boto3.client("secretsmanager")
GITHUB_API = "https://api.github.com"

def get_token():
    arn = os.environ["GITHUB_SECRET_ARN"]
    sec = secrets.get_secret_value(SecretId=arn)["SecretString"]
    return json.loads(sec)["GITHUB_TOKEN"]

def gh_request(method: str, path: str, body=None):
    token = get_token()
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
            "User-Agent": "bedrock-direct-push"
        },
    )

    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def lambda_handler(event, context):
    print(json.dumps(event))

    owner  = os.environ["GITHUB_OWNER"]
    repo   = os.environ["GITHUB_REPO"]
    branch = "main"

    try:
        params = {p["name"]: p["value"] for p in event.get("parameters", [])}
        fn_name = event["function"]

        # ✅ READ file from GitHub
        if fn_name == "read_github_file":
            file_path = params["file_path"].lstrip("/")
            existing = gh_request("GET", f"/repos/{owner}/{repo}/contents/{file_path}?ref={branch}")
            content = base64.b64decode(existing["content"]).decode("utf-8")
            result = {"ok": True, "file_path": file_path, "content": content}

        # ✅ WRITE file to GitHub
        elif fn_name == "push_github_fix":
            import re
            file_path     = params["file_path"].lstrip("/")
            file_content  = params["file_content"]
            commit_message = params.get("commit_message", f"Update {file_path}")

            # Strip markdown code fences if agent wraps content in them
            file_content = re.sub(r'^```[^\n]*\n|```\s*$', '', file_content.strip(), flags=re.MULTILINE)

            existing = gh_request("GET", f"/repos/{owner}/{repo}/contents/{file_path}?ref={branch}")
            file_sha = existing["sha"]

            content_b64 = base64.b64encode(file_content.encode("utf-8")).decode("utf-8")

            updated = gh_request(
                "PUT",
                f"/repos/{owner}/{repo}/contents/{file_path}",
                {
                    "message": commit_message,
                    "content": content_b64,
                    "sha": file_sha,
                    "branch": branch
                }
            )

            result = {
                "ok": True,
                "commit_sha": updated.get("commit", {}).get("sha"),
                "branch": branch
            }

        else:
            result = {"ok": False, "error": f"Unknown function: {fn_name}"}

    except Exception as e:
        result = {"ok": False, "error": str(e)}

    # ✅ Bedrock-compatible response envelope
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event["actionGroup"],
            "function": event["function"],
            "functionResponse": {
                "responseBody": {
                    "TEXT": {
                        "body": json.dumps(result)
                    }
                }
            }
        }
    }
