"""This file handles the fault router lambda function logic for the hack ncstate part of the project."""

import os, json, base64, gzip, urllib.request, urllib.parse, re, time
from datetime import datetime, timezone
import boto3

# ==============================
# 🔥 HACKATHON HARDCODE SECTION
# ==============================

BACKBOARD_BASE_URL = "https://app.backboard.io/api"
HARDCODED_THREAD_ID = "39a2c193-1038-434a-8889-4b874b81bf13"
BACKBOARD_API_KEY = "espr_khwJLso-d0cJdFtfvnDkfQHUPEG50K9-RsOlm_YE9GA"
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

FAULT_CODES = {
    "FAULT_SQL_INJECTION_TEST",
    "FAULT_EXTERNAL_API_LATENCY",
    "FAULT_DB_TIMEOUT"
}

# Skip processing if the demo is paused (set by the Reset endpoint).
DEMO_PAUSE_PARAM = "/cream/demo-paused"

ROUTE_RE = re.compile(r"\broute=([^\s]+)")
REASON_RE = re.compile(r"\breason=([^\s]+)")

# ==============================

# This function handles the decode cw payload work for this file.
def decode_cw_payload(event: dict) -> dict:
    compressed = base64.b64decode(event["awslogs"]["data"])
    decompressed = gzip.decompress(compressed)
    return json.loads(decompressed)

# This function handles the extract fault code work for this file.
def extract_fault_code(msg: str):
    for c in FAULT_CODES:
        if c in msg:
            return c
    return None

# This function builds the incident work used in this file.
def build_incident(le, log_group, log_stream):
    msg = le.get("message", "").strip()
    ts_ms = le.get("timestamp")
    return {
        "event_id": le.get("id"),
        "fault_code": extract_fault_code(msg),
        "timestamp": datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).isoformat(),
        "log_group": log_group,
        "log_stream": log_stream,
        "raw_message": msg
    }


# This function handles the incident dedupe key work for this file.
def incident_dedupe_key(incident: dict) -> str | None:
    fault_code = incident.get("fault_code")
    if not fault_code:
        return None

    msg = incident.get("raw_message", "")
    route_match = ROUTE_RE.search(msg)
    reason_match = REASON_RE.search(msg)
    route = route_match.group(1) if route_match else "-"
    reason = reason_match.group(1) if reason_match else "-"

    return "|".join((fault_code, route, reason))

# This function handles the backboard message work for this file.
def backboard_message(thread_id: str, content: str) -> dict:
    url = f"{BACKBOARD_BASE_URL}/threads/{thread_id}/messages"
    form_data = urllib.parse.urlencode({
        "content": content,
        "llm_provider": "openai",
        "model_name": "gpt-4o",
        "memory": "Auto",
        "stream": "false",
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=form_data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-API-Key": BACKBOARD_API_KEY,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

# This function handles the invoke claude work for this file.
def invoke_claude(incident, analysis):
    tools = [
        {
            "name": "read_github_file",
            "description": "Read the current content of a file from the GitHub repository before making changes.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to read e.g. hello/page/views.py"
                    }
                },
                "required": ["file_path"]
            }
        },
        {
            "name": "push_github_fix",
            "description": "Push a code fix directly to GitHub by updating a file in the repository.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to update e.g. hello/page/views.py"
                    },
                    "file_content": {
                        "type": "string",
                        "description": "The full updated file content to commit"
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Git commit message describing the fix"
                    }
                },
                "required": ["file_path", "file_content", "commit_message"]
            }
        }
    ]

    # Map fault codes to their specific route function names so Claude
    # knows exactly which function to fix and which to leave alone.
    fault_function_map = {
        "FAULT_SQL_INJECTION_TEST": "test_fault_run",
        "FAULT_EXTERNAL_API_LATENCY": "test_fault_external_api",
        "FAULT_DB_TIMEOUT": "test_fault_db_timeout",
    }
    target_function = fault_function_map.get(incident["fault_code"], "unknown")

    fault_fix_hints = {
        "FAULT_SQL_INJECTION_TEST": (
            "The function test_fault_run() executes malformed SQL: `SELECT FROM` "
            "(missing column/table). Fix the SQL query so it executes successfully, "
            "e.g. `SELECT 1`. When the query succeeds, the function should return "
            "status 'ok' (HTTP 200) instead of raising an exception."
        ),
        "FAULT_EXTERNAL_API_LATENCY": (
            "The function test_fault_external_api() calls the mock API with a 3-second "
            "timeout that is too short (the API delays 2-8 seconds). Fix by increasing "
            "the timeout to at least 10 seconds and/or adding retry logic with backoff. "
            "The goal is for the call to succeed instead of timing out."
        ),
        "FAULT_DB_TIMEOUT": (
            "The function test_fault_db_timeout() sets statement_timeout='2s' then runs "
            "pg_sleep(5), which always times out. Fix by either removing the "
            "statement_timeout, increasing it to more than 5 seconds, or reducing the "
            "pg_sleep to less than the timeout. The goal is for the query to complete "
            "without a timeout error."
        ),
    }
    fix_hint = fault_fix_hints.get(incident["fault_code"], "Fix the identified issue.")

    messages = [
        {
            "role": "user",
            "content": f"""You are a remediation agent. You MUST fix ONLY the specific faulty endpoint described below. Do NOT modify any other function in the file.

INCIDENT:
{json.dumps(incident, indent=2)}

BACKBOARD_ANALYSIS:
{json.dumps(analysis, indent=2)}

TARGET FILE: hello/page/views.py
TARGET FUNCTION: {target_function}()
FIX HINT: {fix_hint}

CRITICAL RULES:
1. ONLY modify the function `{target_function}()` and any helper you add for it.
2. Do NOT touch, modify, or "improve" any other function in the file (home, _render_fault, test_fault, or any other test_fault_* function).
3. Every line of code outside `{target_function}()` must remain EXACTLY as-is — same imports, same logic, same comments, same bugs. If another function has a bug, LEAVE IT. You are only fixing {incident['fault_code']}.
4. Your commit message MUST start with "[FAULT:{incident['fault_code']}]".

Steps:
1. Call read_github_file to read hello/page/views.py
2. Identify the bug in `{target_function}()` only
3. Fix ONLY that function — copy everything else unchanged
4. Call push_github_fix with the full file (commit message starts with [FAULT:{incident['fault_code']}])
5. Report what you changed"""
        }
    ]

    # Agentic loop - keep going until Claude stops calling tools
    while True:
        body = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 8192,
            "tools": tools,
            "messages": messages
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                response = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            print(f"CLAUDE_ERROR: {error_body}")
            raise

        stop_reason = response.get("stop_reason")
        content_blocks = response.get("content", [])

        # Append assistant response to messages
        messages.append({"role": "assistant", "content": content_blocks})

        # Check if Claude wants to call tools
        tool_use_blocks = [b for b in content_blocks if b["type"] == "tool_use"]

        if not tool_use_blocks:
            # No tool calls - extract text response
            for block in content_blocks:
                if block["type"] == "text":
                    return block["text"]
            return "Remediation complete."

        # Handle tool calls
        tool_results = []
        lambda_client = boto3.client("lambda")

        for block in tool_use_blocks:
            tool_name = block["name"]
            tool_input = block.get("input", {})
            tool_use_id = block["id"]

            print(f"TOOL_CALL: {tool_name} -> {json.dumps(tool_input)}")

            github_event = {
                "actionGroup": "GitHubActions",
                "function": tool_name,
                "parameters": [
                    {"name": k, "value": v}
                    for k, v in tool_input.items()
                ]
            }

            github_resp = lambda_client.invoke(
                FunctionName=os.environ["GITHUB_LAMBDA_NAME"],
                Payload=json.dumps(github_event).encode("utf-8")
            )

            payload = json.loads(github_resp["Payload"].read())
            result_body = payload["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
            print(f"TOOL_RESULT: {result_body[:2000]}")

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result_body
            })

        # Feed tool results back to Claude
        messages.append({"role": "user", "content": tool_results})

FAULT_COOLDOWN_SECONDS = 600  # 10-minute cooldown per fault code

def _check_and_set_cooldown(fault_code: str) -> bool:
    """Return True if this fault was already processed recently (skip it)."""
    ssm = boto3.client("ssm")
    param_name = f"/cream/fault-cooldown/{fault_code}"
    try:
        resp = ssm.get_parameter(Name=param_name)
        last_ts = float(resp["Parameter"]["Value"])
        if time.time() - last_ts < FAULT_COOLDOWN_SECONDS:
            return True
    except ssm.exceptions.ParameterNotFound:
        pass
    except Exception as e:
        print(f"SSM_READ_ERROR: {e}")
    ssm.put_parameter(Name=param_name, Value=str(time.time()), Type="String", Overwrite=True)
    return False


def _is_demo_paused() -> bool:
    """Check if demo pause is active (set by /developer/incidents/reset)."""
    ssm = boto3.client("ssm")
    try:
        resp = ssm.get_parameter(Name=DEMO_PAUSE_PARAM)
        return resp["Parameter"]["Value"] == "true"
    except ssm.exceptions.ParameterNotFound:
        return False
    except Exception as e:
        print(f"DEMO_PAUSE_CHECK_ERROR: {e}")
        return False


# This function handles the lambda handler work for this file.
def lambda_handler(event, context):
    if _is_demo_paused():
        print("SKIP: demo is paused — faults will not be auto-remediated")
        return {"statusCode": 200, "body": "demo_paused"}

    cw = decode_cw_payload(event)
    log_group = cw.get("logGroup")
    log_stream = cw.get("logStream")
    processed_incidents = set()

    for le in cw.get("logEvents", []):
        inc = build_incident(le, log_group, log_stream)

        if not inc["fault_code"]:
            continue

        dedupe_key = incident_dedupe_key(inc)
        if dedupe_key in processed_incidents:
            print(f"SKIP duplicate incident in batch: {dedupe_key}")
            continue
        processed_incidents.add(dedupe_key)

        if _check_and_set_cooldown(inc["fault_code"]):
            print(f"SKIP cooldown active for {inc['fault_code']} (within {FAULT_COOLDOWN_SECONDS}s)")
            continue

        try:
            # 1️⃣ Send incident to Backboard thread → get RAG analysis
            analysis = backboard_message(
                HARDCODED_THREAD_ID,
                (
                    f"Fault detected: {inc['fault_code']}\n"
                    f"Timestamp: {inc['timestamp']}\n"
                    f"Log group: {inc['log_group']}\n"
                    f"Message: {inc['raw_message']}"
                ),
            )
            print("BACKBOARD_ANALYSIS:", json.dumps(analysis)[:4000])

            # 2️⃣ Call Claude API with GitHub tools
            agent_output = invoke_claude(inc, analysis)
            print("CLAUDE_OUTPUT:", agent_output[:4000])

            # 3️⃣ Record pending remediation on dashboard so the pipeline
            #    callback (GitHub Actions) can resolve it after deploy.
            dashboard_url = os.environ.get("DASHBOARD_URL", "")
            if dashboard_url:
                try:
                    route_match = ROUTE_RE.search(inc.get("raw_message", ""))
                    reason_match = REASON_RE.search(inc.get("raw_message", ""))
                    record_body = json.dumps({
                        "fault_code": inc["fault_code"],
                        "route": route_match.group(1) if route_match else "",
                        "reason": reason_match.group(1) if reason_match else "",
                        "rag_analysis": json.dumps(analysis)[:2000],
                        "claude_output": agent_output[:2000],
                    }).encode("utf-8")
                    req = urllib.request.Request(
                        f"{dashboard_url}/developer/incidents/pipeline/pending",
                        data=record_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    urllib.request.urlopen(req, timeout=10)
                    print(f"DASHBOARD: recorded pending remediation for {inc['fault_code']}")
                except Exception as e:
                    print(f"DASHBOARD_RECORD_ERROR: {e}")

            # 4️⃣ Post remediation back to Backboard thread
            backboard_message(
                HARDCODED_THREAD_ID,
                f"Remediation applied for {inc['fault_code']}:\n{agent_output}",
            )

        except Exception as e:
            import traceback
            print(f"ERROR processing {inc['fault_code']}: {e}")
            print(traceback.format_exc())
            continue

    return {"statusCode": 200, "body": "ok"}
