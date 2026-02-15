import os, json, base64, gzip, urllib.request, urllib.parse, re
from datetime import datetime, timezone
import boto3

# ==============================
# 🔥 HACKATHON HARDCODE SECTION
# ==============================

BACKBOARD_BASE_URL = "https://app.backboard.io/api"
HARDCODED_THREAD_ID = "39a2c193-1038-434a-8889-4b874b81bf13"
BACKBOARD_API_KEY = "espr_khwJLso-d0cJdFtfvnDkfQHUPEG50K9-RsOlm_YE9GA"
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

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

def invoke_gemini(incident, analysis):
    tools = [
        {
            "function_declarations": [
                {
                    "name": "read_github_file",
                    "description": "Read the current content of a file from the GitHub repository before making changes.",
                    "parameters": {
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
                    "parameters": {
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
        }
    ]

    contents = [
        {
            "role": "user",
            "parts": [{"text": f"""You are a remediation agent. Analyze the incident and push a fix to GitHub.

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
4. Report what you changed"""}]
        }
    ]

    # Agentic loop - keep going until Gemini stops calling tools
    while True:
        body = json.dumps({
            "contents": contents,
            "tools": tools
        }).encode("utf-8")

        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                response = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            print(f"GEMINI_ERROR: {error_body}")
            raise

        candidate = response["candidates"][0]
        contents.append(candidate["content"])
        parts = candidate["content"].get("parts", [])

        # Check if Gemini wants to call a tool
        tool_calls = [p for p in parts if "functionCall" in p]

        if not tool_calls:
            # No tool calls - return the text response
            for part in parts:
                if "text" in part:
                    return part["text"]
            return "Remediation complete."

        # Handle tool calls
        tool_results = []
        lambda_client = boto3.client("lambda")

        for part in tool_calls:
            fn = part["functionCall"]
            fn_name = fn["name"]
            fn_args = fn.get("args", {})

            print(f"TOOL_CALL: {fn_name} → {json.dumps(fn_args)}")

            github_event = {
                "actionGroup": "GitHubActions",
                "function": fn_name,
                "parameters": [
                    {"name": k, "value": v}
                    for k, v in fn_args.items()
                ]
            }

            github_resp = lambda_client.invoke(
                FunctionName=os.environ["GITHUB_LAMBDA_NAME"],
                Payload=json.dumps(github_event).encode("utf-8")
            )

            payload = json.loads(github_resp["Payload"].read())
            result_body = payload["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]
            print(f"TOOL_RESULT: {result_body}")

            tool_results.append({
                "functionResponse": {
                    "name": fn_name,
                    "response": {"result": result_body}
                }
            })

        # Feed tool results back to Gemini
        contents.append({
            "role": "user",
            "parts": tool_results
        })

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

            # 2️⃣ Call Gemini API with GitHub tools
            agent_output = invoke_gemini(inc, analysis)
            print("GEMINI_OUTPUT:", agent_output[:4000])

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
