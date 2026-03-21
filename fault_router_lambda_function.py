import os, json, base64, gzip, urllib.request, urllib.parse, re
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

# ==============================

def decode_cw_payload(event: dict) -> dict:
    compressed = base64.b64decode(event["awslogs"]["data"])
    decompressed = gzip.decompress(compressed)
    return json.loads(decompressed)

def extract_fault_code(msg: str):
    for c in FAULT_CODES:
        if c in msg:
            return c
    return None

def build_incident(le, log_group, log_stream):
    msg = le.get("message", "").strip()
    ts_ms = le.get("timestamp")
    return {
        "fault_code": extract_fault_code(msg),
        "timestamp": datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).isoformat(),
        "log_group": log_group,
        "log_stream": log_stream,
        "raw_message": msg
    }

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

    messages = [
        {
            "role": "user",
            "content": f"""You are a remediation agent. Analyze the incident and push a fix to GitHub.

INCIDENT:
{json.dumps(incident, indent=2)}

BACKBOARD_ANALYSIS:
{json.dumps(analysis, indent=2)}

The repository has the following key file you must use when pushing fixes:
- hello/page/views.py  (no leading slash)

Steps you MUST follow:
1. First call read_github_file to read the actual content of hello/page/views.py
2. Analyze the file content and identify the vulnerability
3. Call push_github_fix with the complete fixed file content
4. Report what you changed"""
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

def lambda_handler(event, context):
    cw = decode_cw_payload(event)
    log_group = cw.get("logGroup")
    log_stream = cw.get("logStream")

    for le in cw.get("logEvents", []):
        inc = build_incident(le, log_group, log_stream)

        if not inc["fault_code"]:
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

            # 3️⃣ Post remediation back to Backboard thread
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
