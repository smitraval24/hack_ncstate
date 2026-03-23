"""This file handles the github tool lambda function logic for the hack ncstate part of the project."""

import base64
import difflib
import json
import os
import urllib.request
import boto3

GITHUB_API = "https://api.github.com"
ALLOWED_FILE_PATHS = {
    "hello/page/views_sql.py",
    "hello/page/views_api.py",
    "hello/page/views_db.py",
}
FORBIDDEN_CONTEXT_FILE_PATHS = {
    "hello/page/_faulty_views_template.py",
    "hello/page/views.py",
}
APPROVED_LINE_REPLACEMENTS = {
    "hello/page/views_sql.py": {
        "old": '        db.session.execute(text("SELECT FROM"))',
        "new": '        db.session.execute(text("SELECT 1"))',
    },
    "hello/page/views_api.py": {
        "old": '        r = requests.get(f"{mock_api_base_url}/data", timeout=3)',
        "new": '        r = requests.get(f"{mock_api_base_url}/data", timeout=10)',
    },
    "hello/page/views_db.py": {
        "old": '        db.session.execute(text("SELECT pg_sleep(10);"))',
        "new": '        db.session.execute(text("SELECT pg_sleep(1);"))',
    },
}


def normalize_file_path(file_path: str) -> str:
    """Normalize incoming file paths before validation."""
    return file_path.lstrip("/")


def validate_file_path(file_path: str, allowed_file_path: str | None = None) -> str:
    """Allow only approved remediation files, optionally scoped to one exact file."""
    normalized = normalize_file_path(file_path)
    allowed_normalized = (
        normalize_file_path(allowed_file_path) if allowed_file_path else None
    )

    if normalized in FORBIDDEN_CONTEXT_FILE_PATHS:
        raise ValueError(
            f"Access to {normalized} is forbidden for remediation context"
        )
    if allowed_normalized:
        if allowed_normalized in FORBIDDEN_CONTEXT_FILE_PATHS:
            raise ValueError(
                f"Configured allowed file {allowed_normalized} is forbidden"
            )
        if allowed_normalized not in ALLOWED_FILE_PATHS:
            raise ValueError(
                f"Configured allowed file {allowed_normalized} is not a valid remediation target"
            )
        if normalized != allowed_normalized:
            raise ValueError(
                f"This invocation may only access {allowed_normalized}; received {normalized}"
            )
        return normalized
    if normalized not in ALLOWED_FILE_PATHS:
        raise ValueError(
            f"Only {', '.join(sorted(ALLOWED_FILE_PATHS))} can be accessed by this tool"
        )

    return normalized


def _strip_line_ending(value: str) -> str:
    return value.rstrip("\r\n")


def validate_commit_message(commit_message: str, fault_code: str | None) -> None:
    """Require commits to stay tied to the routed fault code."""
    if not fault_code:
        return
    expected_prefix = f"[FAULT:{fault_code}]"
    if not commit_message.startswith(expected_prefix):
        raise ValueError(
            f"Commit message must start with {expected_prefix}"
        )


def validate_approved_patch(
    file_path: str,
    existing_content: str,
    requested_content: str,
) -> None:
    """Allow only the exact approved one-line remediation per routed file."""
    approved = APPROVED_LINE_REPLACEMENTS.get(file_path)
    if not approved:
        raise ValueError(f"No approved remediation rule configured for {file_path}")

    existing_lines = existing_content.splitlines()
    requested_lines = requested_content.splitlines()
    changes = [
        opcode
        for opcode in difflib.SequenceMatcher(
            a=existing_lines,
            b=requested_lines,
            autojunk=False,
        ).get_opcodes()
        if opcode[0] != "equal"
    ]

    if len(changes) != 1:
        raise ValueError(
            "Remediation must change exactly one contiguous line in the approved file"
        )

    tag, i1, i2, j1, j2 = changes[0]
    if tag != "replace" or (i2 - i1) != 1 or (j2 - j1) != 1:
        raise ValueError(
            "Remediation must replace exactly one line with the approved fix"
        )

    existing_line = _strip_line_ending(existing_lines[i1])
    requested_line = _strip_line_ending(requested_lines[j1])

    if existing_line != approved["old"]:
        raise ValueError(
            f"Existing file does not contain the expected buggy line for {file_path}"
        )
    if requested_line != approved["new"]:
        raise ValueError(
            f"Requested change does not match the approved one-line fix for {file_path}"
        )

# This function gets the token data the rest of the code needs.
def get_token():
    arn = os.environ["GITHUB_SECRET_ARN"]
    secrets = boto3.client("secretsmanager")
    sec = secrets.get_secret_value(SecretId=arn)["SecretString"]
    return json.loads(sec)["GITHUB_TOKEN"]

# This function handles the gh request work for this file.
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

# This function handles the lambda handler work for this file.
def lambda_handler(event, context):
    print(json.dumps(event))

    owner  = os.environ["GITHUB_OWNER"]
    repo   = os.environ["GITHUB_REPO"]
    branch = "main"

    try:
        params = {p["name"]: p["value"] for p in event.get("parameters", [])}
        fn_name = event["function"]
        allowed_file_path = event.get("allowed_file_path")
        fault_code = event.get("fault_code")

        # ✅ READ file from GitHub
        if fn_name == "read_github_file":
            file_path = validate_file_path(
                params["file_path"],
                allowed_file_path=allowed_file_path,
            )
            existing = gh_request("GET", f"/repos/{owner}/{repo}/contents/{file_path}?ref={branch}")
            content = base64.b64decode(existing["content"]).decode("utf-8")
            result = {"ok": True, "file_path": file_path, "content": content}

        # ✅ WRITE file to GitHub
        elif fn_name == "push_github_fix":
            import re
            file_path     = validate_file_path(
                params["file_path"],
                allowed_file_path=allowed_file_path,
            )
            file_content  = params["file_content"]
            commit_message = params.get("commit_message", f"Update {file_path}")
            validate_commit_message(commit_message, fault_code)

            # Strip markdown code fences if agent wraps content in them
            file_content = re.sub(r'^```[^\n]*\n|```\s*$', '', file_content.strip(), flags=re.MULTILINE)

            existing = gh_request("GET", f"/repos/{owner}/{repo}/contents/{file_path}?ref={branch}")
            file_sha = existing["sha"]
            existing_content = base64.b64decode(existing["content"]).decode("utf-8")
            normalized_existing = existing_content.rstrip("\n")
            normalized_requested = file_content.rstrip("\n")

            if normalized_existing == normalized_requested:
                result = {
                    "ok": True,
                    "commit_sha": None,
                    "branch": branch,
                    "no_change": True
                }
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

            validate_approved_patch(file_path, existing_content, file_content)
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
