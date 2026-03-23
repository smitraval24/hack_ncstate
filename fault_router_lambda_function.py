"""This file handles the fault router lambda function logic for the hack ncstate part of the project."""

import os, json, base64, gzip, urllib.request, urllib.parse, re, time
from datetime import datetime, timezone
from pathlib import Path
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
# Each fault code maps to its own isolated file so Claude can only see/edit one fault at a time
FAULT_FILE_MAP = {
    "FAULT_SQL_INJECTION_TEST": "hello/page/views_sql.py",
    "FAULT_EXTERNAL_API_LATENCY": "hello/page/views_api.py",
    "FAULT_DB_TIMEOUT": "hello/page/views_db.py",
}
FAULT_FUNCTION_MAP = {
    "FAULT_SQL_INJECTION_TEST": "test_fault_run",
    "FAULT_EXTERNAL_API_LATENCY": "test_fault_external_api",
    "FAULT_DB_TIMEOUT": "test_fault_db_timeout",
}
FAULT_SOLUTION_FILE_MAP = {
    "FAULT_SQL_INJECTION_TEST": "claude_solutions/fault_sql_solution.txt",
    "FAULT_EXTERNAL_API_LATENCY": "claude_solutions/fault_api_solution.txt",
    "FAULT_DB_TIMEOUT": "claude_solutions/fault_db_solution.txt",
}
BASE_DIR = Path(__file__).resolve().parent


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


def load_solution_context(fault_code: str) -> str:
    """Load the packaged known-good solution notes for a fault code."""
    relative_path = FAULT_SOLUTION_FILE_MAP.get(fault_code)
    if not relative_path:
        return ""

    try:
        return (BASE_DIR / relative_path).read_text(encoding="utf-8").strip()
    except Exception as exc:
        print(f"SOLUTION_CONTEXT_ERROR {fault_code}: {exc}")
        return ""


def build_claude_prompt(
    incident: dict,
    analysis: dict,
    target_file: str,
    target_function: str,
    solution_context: str,
) -> str:
    """Build the Claude remediation prompt with packaged solution guidance."""
    solution_section = solution_context or "No packaged solution notes found."

    return f"""You are a remediation agent. You fix bugs with the SMALLEST possible change.

INCIDENT:
{json.dumps(incident, indent=2)}

TARGET FILE: {target_file}
TARGET FUNCTION: {target_function}()

KNOWN_GOOD_SOLUTION:
{solution_section}

RAG CONTEXT (reference only — do NOT use this to expand your fix scope):
{json.dumps(analysis, indent=2)}

════════════════════════════════════════════════════════════════
HARDCODED FILE ROUTING — ABSOLUTELY NON-NEGOTIABLE:
════════════════════════════════════════════════════════════════
- FAULT_SQL_INJECTION_TEST  → ONLY hello/page/views_sql.py
- FAULT_EXTERNAL_API_LATENCY → ONLY hello/page/views_api.py
- FAULT_DB_TIMEOUT          → ONLY hello/page/views_db.py

FORBIDDEN FILES — NEVER read, write, reference, or touch:
- hello/page/views.py             ← FORBIDDEN
- hello/page/_faulty_views_template.py ← FORBIDDEN
- Any file not listed in the routing above ← FORBIDDEN

You may ONLY call read_github_file and push_github_fix with
file_path set to EXACTLY: {target_file}
Any other file path will be rejected and is a violation.
════════════════════════════════════════════════════════════════

RULES:
1. Call read_github_file with file_path="{target_file}" (EXACTLY this path, no other).
2. Identify the ONE buggy line described in the solution notes.
3. Change ONLY that line. Your diff must be 1-3 lines maximum.
4. The RAG CONTEXT is background information only. Do NOT implement any suggestions from it that go beyond the solution notes.
5. Do NOT add new functions, classes, imports, retry logic, or validation.
6. Do NOT restructure, refactor, or rewrite surrounding code.
7. Every line you did not change must remain byte-for-byte identical.
8. Call push_github_fix with file_path="{target_file}" (EXACTLY this path, no other).
9. Your commit message MUST start with "[FAULT:{incident['fault_code']}]".
10. NEVER access hello/page/views.py — it is NOT a remediation target.

IMPORTANT: If your change touches more than 3 lines, you are doing too much. Stop and reconsider."""


def validate_tool_input(tool_name: str, tool_input: dict, target_file: str) -> dict:
    """Reject any tool call that tries to read or write outside the routed file."""
    requested_path = tool_input.get("file_path")
    if not requested_path:
        raise ValueError(f"{tool_name} requires file_path")

    normalized_path = requested_path.lstrip("/")
    if normalized_path != target_file:
        raise ValueError(
            f"{tool_name} may only access {target_file}; received {normalized_path}"
        )

    sanitized_input = dict(tool_input)
    sanitized_input["file_path"] = normalized_path
    return sanitized_input


def build_github_tool_event(
    tool_name: str,
    tool_input: dict,
    target_file: str,
    fault_code: str,
) -> dict:
    """Build a GitHub Lambda event with server-side file scope enforcement."""
    sanitized_input = validate_tool_input(tool_name, tool_input, target_file)

    return {
        "actionGroup": "GitHubActions",
        "function": tool_name,
        "allowed_file_path": target_file,
        "fault_code": fault_code,
        "parameters": [
            {"name": key, "value": value}
            for key, value in sanitized_input.items()
        ],
    }

# This function handles the invoke claude work for this file.
def invoke_claude(incident, analysis):
    # Each fault code has its own isolated file — Claude only sees that one file
    target_file = FAULT_FILE_MAP.get(incident["fault_code"], "hello/page/views_sql.py")

    tools = [
        {
            "name": "read_github_file",
            "description": (
                f"Read the current content of {target_file} from the GitHub "
                "repository before making changes. Do not read any other fault files, "
                "template files, or fault reference files."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": f"Must be exactly {target_file}"
                    }
                },
                "required": ["file_path"]
            }
        },
        {
            "name": "push_github_fix",
            "description": f"Push a code fix directly to GitHub by updating {target_file}.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": f"Path to the file to update — must be {target_file}"
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

    # HARDCODED SAFETY: reject any fault code that doesn't map to a known file
    if incident["fault_code"] not in FAULT_FILE_MAP:
        raise ValueError(f"Unknown fault code: {incident['fault_code']} — no file mapping exists")

    target_function = FAULT_FUNCTION_MAP.get(incident["fault_code"], "unknown")
    solution_context = load_solution_context(incident["fault_code"])

    messages = [
        {
            "role": "user",
            "content": build_claude_prompt(
                incident=incident,
                analysis=analysis,
                target_file=target_file,
                target_function=target_function,
                solution_context=solution_context,
            ),
        }
    ]

    # Inject system-level constraint so Claude cannot override it
    system_prompt = (
        f"You are a file-scoped remediation agent. "
        f"You may ONLY access the file: {target_file}. "
        f"NEVER access, read, write, or reference hello/page/views.py or any file "
        f"other than {target_file}. Any tool call with a different file_path WILL be rejected."
    )

    # Agentic loop - keep going until Claude stops calling tools
    while True:
        body = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2048,
            "system": system_prompt,
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

            try:
                github_event = build_github_tool_event(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    target_file=target_file,
                    fault_code=incident["fault_code"],
                )
            except Exception as exc:
                result_body = json.dumps({"ok": False, "error": str(exc)})
                print(f"TOOL_RESULT: {result_body[:2000]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_body
                })
                continue

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
SELF_HEALING_PAUSED_PARAM = "/cream/self-healing-paused"


def _is_self_healing_paused() -> bool:
    """Return True if the self-healing loop is paused via SSM."""
    ssm = boto3.client("ssm")
    try:
        ssm.get_parameter(Name=SELF_HEALING_PAUSED_PARAM)
        return True
    except ssm.exceptions.ParameterNotFound:
        return False
    except Exception as e:
        print(f"SSM_PAUSE_CHECK_ERROR: {e}")
        return False


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


# This function handles the lambda handler work for this file.
def lambda_handler(event, context):
    if _is_self_healing_paused():
        print("SKIP: self-healing loop is paused")
        return {"statusCode": 200, "body": "paused"}

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
