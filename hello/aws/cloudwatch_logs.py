from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


@dataclass(frozen=True)
class CloudWatchLogEvent:
    log_group: str
    log_stream: str
    timestamp_ms: int
    message: str

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp_ms / 1000, tz=UTC)


_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


def _cache_get(key: tuple[Any, ...]) -> Any | None:
    now = time.time()
    entry = _CACHE.get(key)
    if not entry:
        return None
    if entry["expires_at"] <= now:
        _CACHE.pop(key, None)
        return None
    return entry["value"]


def _cache_set(key: tuple[Any, ...], value: Any, ttl_seconds: int) -> None:
    _CACHE[key] = {
        "expires_at": time.time() + ttl_seconds,
        "value": value,
    }


def get_cloudwatch_region(default: str = "us-east-1") -> str:
    return (
        os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or default
    )


def get_cloudwatch_log_groups() -> list[str]:
    """Return configured log groups.

    Supported env vars:
    - CLOUDWATCH_LOG_GROUPS: comma-separated list
    - CLOUDWATCH_LOG_GROUP: single group (legacy)
    """
    groups = os.getenv("CLOUDWATCH_LOG_GROUPS")
    if groups:
        return [g.strip() for g in groups.split(",") if g.strip()]

    legacy = os.getenv("CLOUDWATCH_LOG_GROUP")
    if legacy:
        return [legacy]

    return []


def fetch_recent_events(
    *,
    log_groups: list[str],
    lookback: timedelta = timedelta(minutes=60),
    filter_pattern: str | None = None,
    limit_per_group: int = 200,
    max_pages_per_group: int = 5,
    cache_ttl_seconds: int = 15,
) -> list[CloudWatchLogEvent]:
    """Fetch recent CloudWatch Log events from one or more log groups.

    Uses FilterLogEvents and caps pagination to keep calls bounded.
    """
    if not log_groups:
        return []

    region = get_cloudwatch_region()
    start_time = datetime.now(tz=UTC) - lookback
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(datetime.now(tz=UTC).timestamp() * 1000)

    key = (
        "events",
        region,
        tuple(log_groups),
        start_ms // 1000,
        end_ms // 1000,
        filter_pattern,
        int(limit_per_group),
        int(max_pages_per_group),
    )
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # Small timeouts; this endpoint is used to render HTML.
    boto_config = Config(
        read_timeout=3,
        connect_timeout=2,
        retries={"max_attempts": 2, "mode": "standard"},
    )
    client = boto3.client("logs", region_name=region, config=boto_config)

    def _describe_recent_streams(
        log_group_name: str,
        *,
        max_streams: int,
    ) -> list[dict[str, Any]]:
        resp = client.describe_log_streams(
            logGroupName=log_group_name,
            orderBy="LastEventTime",
            descending=True,
            limit=max_streams,
        )
        return resp.get("logStreams", []) or []

    def _get_stream_events(
        log_group_name: str,
        log_stream_name: str,
        *,
        start_time_ms: int,
        end_time_ms: int,
        limit: int,
    ) -> list[CloudWatchLogEvent]:
        resp = client.get_log_events(
            logGroupName=log_group_name,
            logStreamName=log_stream_name,
            startTime=start_time_ms,
            endTime=end_time_ms,
            startFromHead=False,
            limit=limit,
        )
        out: list[CloudWatchLogEvent] = []
        for e in resp.get("events", []) or []:
            message = (e.get("message") or "").strip("\n")
            if not message:
                continue
            out.append(
                CloudWatchLogEvent(
                    log_group=log_group_name,
                    log_stream=log_stream_name,
                    timestamp_ms=int(e.get("timestamp") or 0),
                    message=message,
                )
            )
        return out

    events: list[CloudWatchLogEvent] = []
    for group in log_groups:
        group_events: list[CloudWatchLogEvent] = []
        next_token: str | None = None
        pages = 0
        while pages < max_pages_per_group:
            pages += 1
            params: dict[str, Any] = {
                "logGroupName": group,
                "startTime": start_ms,
                "endTime": end_ms,
                "interleaved": True,
                "limit": limit_per_group,
            }
            if filter_pattern:
                params["filterPattern"] = filter_pattern
            if next_token:
                params["nextToken"] = next_token

            try:
                resp = client.filter_log_events(**params)
            except ClientError as e:
                err = e.response.get("Error", {}) if hasattr(e, "response") else {}
                code = err.get("Code") or "ClientError"
                msg = err.get("Message") or str(e)
                raise RuntimeError(
                    f"CloudWatch Logs filter_log_events failed for {group}: {code}: {msg}"
                ) from e
            except BotoCoreError as e:
                raise RuntimeError(
                    f"CloudWatch Logs filter_log_events failed for {group}: {e}"
                ) from e

            for e in resp.get("events", []) or []:
                message = (e.get("message") or "").strip("\n")
                if not message:
                    continue
                group_events.append(
                    CloudWatchLogEvent(
                        log_group=group,
                        log_stream=e.get("logStreamName") or "",
                        timestamp_ms=int(e.get("timestamp") or 0),
                        message=message,
                    )
                )

            token = resp.get("nextToken")
            if not token or token == next_token:
                break
            next_token = token

        # If FilterLogEvents returns nothing, fall back to GetLogEvents on the newest streams.
        # This helps when the filter index lags behind recent ingestions.
        if not group_events:
            try:
                max_streams = int(os.getenv("CLOUDWATCH_MAX_STREAMS_PER_GROUP", "5"))
            except Exception:
                max_streams = 5
            max_streams = max(1, min(max_streams, 20))

            per_stream_limit = max(10, min(200, limit_per_group // max_streams or 50))

            try:
                streams = _describe_recent_streams(group, max_streams=max_streams)
                for s in streams:
                    stream_name = s.get("logStreamName")
                    if not stream_name:
                        continue
                    group_events.extend(
                        _get_stream_events(
                            group,
                            stream_name,
                            start_time_ms=start_ms,
                            end_time_ms=end_ms,
                            limit=per_stream_limit,
                        )
                    )
            except ClientError as e:
                err = e.response.get("Error", {}) if hasattr(e, "response") else {}
                code = err.get("Code") or "ClientError"
                msg = err.get("Message") or str(e)
                raise RuntimeError(
                    f"CloudWatch Logs get_log_events fallback failed for {group}: {code}: {msg}"
                ) from e
            except BotoCoreError as e:
                raise RuntimeError(
                    f"CloudWatch Logs get_log_events fallback failed for {group}: {e}"
                ) from e

        events.extend(group_events)

    events.sort(key=lambda ev: ev.timestamp_ms, reverse=True)
    _cache_set(key, events, ttl_seconds=cache_ttl_seconds)
    return events


_FAULT_RE = re.compile(r"\b(FAULT_[A-Z0-9_]+)\b")
_ROUTE_RE = re.compile(r"\broute=([^\s]+)")
_LATENCY_RE = re.compile(r"\blatency=([0-9]+\.[0-9]+)")
_DASHBOARD_RE = re.compile(r"^DASHBOARD\s+(.+?)\s+failed:")
_START_REQ_RE = re.compile(r"^START RequestId:\s*([a-f0-9-]+)")
_END_REQ_RE = re.compile(r"^END RequestId:\s*([a-f0-9-]+)")
_ERROR_PROCESSING_RE = re.compile(r"^ERROR processing\s+(FAULT_[A-Z0-9_]+):")


def get_fault_codes() -> set[str]:
    """Fault codes the dashboard should consider as incidents.

    Defaults to the same codes your FaultRouter Lambda uses.
    Override via CLOUDWATCH_FAULT_CODES (comma-separated).
    """
    raw = os.getenv(
        "CLOUDWATCH_FAULT_CODES",
        "FAULT_SQL_INJECTION_TEST,FAULT_EXTERNAL_API_LATENCY,FAULT_DB_TIMEOUT",
    )
    return {c.strip() for c in raw.split(",") if c.strip()}


def _extract_fault_code_any(message: str, allowed: set[str]) -> str | None:
    for code in allowed:
        if code in message:
            return code
    return None


def _parse_backboard_analysis_message(msg: str) -> dict[str, Any] | None:
    if not msg.startswith("BACKBOARD_ANALYSIS:"):
        return None
    raw = msg[len("BACKBOARD_ANALYSIS:") :].strip()
    try:
        return json.loads(raw)
    except Exception:
        return None


def _extract_http_error_code(msg: str) -> str | None:
    # Example: <HTTPError 405: 'METHOD NOT ALLOWED'>
    m = re.search(r"<HTTPError\s+(\d+):", msg)
    return m.group(1) if m else None


def build_fault_router_incidents(
    events: list[CloudWatchLogEvent],
    *,
    max_incidents: int = 50,
    logs_per_incident: int = 10,
) -> list[dict[str, Any]]:
    """Reconstruct incidents from FaultRouter Lambda logs.

    This uses the Lambda's own log group (/aws/lambda/FaultRouter) and tries
    to rebuild the incident lifecycle that the Lambda executes:
    - detects a FAULT_* (implicit)
    - obtains BACKBOARD analysis (logged as BACKBOARD_ANALYSIS: {...})
    - runs Gemini remediation (logged as GEMINI_OUTPUT: ...)

    Output is shaped to the Developer dashboard schema.
    """
    if not events:
        return []

    allowed_faults = get_fault_codes()

    # Process chronologically to keep request context.
    ordered = sorted(events, key=lambda ev: ev.timestamp_ms)

    # state
    request_id: str | None = None
    last_fault_by_request: dict[str, str] = {}
    incidents: dict[str, dict[str, Any]] = {}

    def _get_or_create_incident(fault_code: str, opened_at: datetime) -> dict[str, Any]:
        inc = incidents.get(fault_code)
        if inc:
            return inc

        incident_id = f"FR-{_stable_id(fault_code, str(int(opened_at.timestamp())))}"
        inc = {
            "id": incident_id,
            "timestamp_opened": opened_at.replace(tzinfo=None),
            "timestamp_resolved": None,
            "incident_type": _guess_type(fault_code),
            "severity": _guess_severity(fault_code, fault_code),
            "status": "detected",
            "route": "-",
            "error_code": fault_code,
            "symptoms": {
                "error_rate": "—",
                "error_rate_value": 1,
                "latency_p95": "—",
                "latency_p95_value": 0.0,
                "endpoint": "-",
                "log_marker": fault_code,
                "affected_requests": 1,
            },
            "breadcrumbs": {
                "recent_logs": [],
                "metric_snapshot": {
                    "total_requests": None,
                    "failed_requests": 1,
                    "avg_latency": None,
                    "timestamp": opened_at.isoformat(),
                },
                "correlated_events": ["log_group=/aws/lambda/FaultRouter"],
            },
            "root_cause": {
                "source": "faultrouter",
                "confidence_score": None,
                "explanation": "Pending Backboard analysis",
            },
            "remediation": {
                "action_type": None,
                "parameters": None,
                "execution_timestamp": None,
            },
            "verification": {
                "error_rate_before": None,
                "error_rate_after": None,
                "latency_before": None,
                "latency_after": None,
                "health_check_status": None,
                "success": None,
            },
        }
        incidents[fault_code] = inc
        return inc

    for ev in ordered:
        msg = (ev.message or "").strip()
        if not msg:
            continue

        # Request context.
        m = _START_REQ_RE.match(msg)
        if m:
            request_id = m.group(1)
            continue
        m = _END_REQ_RE.match(msg)
        if m:
            if request_id == m.group(1):
                request_id = None
            continue

        # When the Lambda logs explicit processing errors, capture the fault code.
        m = _ERROR_PROCESSING_RE.match(msg)
        if m:
            fault_code = m.group(1)
            if fault_code not in allowed_faults:
                continue
            inc = _get_or_create_incident(fault_code, ev.timestamp)
            inc["status"] = "in_progress"
            inc["root_cause"]["explanation"] = "FaultRouter failed while processing this incident"
            inc["breadcrumbs"]["recent_logs"].append(f"{ev.timestamp.isoformat()} {msg}")
            if request_id:
                last_fault_by_request[request_id] = fault_code
            continue

        # Backboard analysis payload: treat as "analyzed" stage.
        analysis = _parse_backboard_analysis_message(msg)
        if analysis:
            content = analysis.get("content") or ""
            if not isinstance(content, str):
                content = str(content)

            fault_code = _extract_fault_code_any(content, allowed_faults)
            if not fault_code:
                # Sometimes Backboard replies may not mention the code.
                # Try to associate with current request.
                if request_id:
                    fault_code = last_fault_by_request.get(request_id)
            if not fault_code:
                continue

            inc = _get_or_create_incident(fault_code, ev.timestamp)
            inc["status"] = "in_progress"
            inc["root_cause"] = {
                "source": "backboard",
                "confidence_score": None,
                "explanation": content,
            }
            inc["breadcrumbs"]["recent_logs"].append(
                f"{ev.timestamp.isoformat()} BACKBOARD_ANALYSIS"
            )
            tid = analysis.get("thread_id")
            if tid:
                inc["breadcrumbs"]["correlated_events"].append(f"thread_id={tid}")
            if request_id:
                last_fault_by_request[request_id] = fault_code
            continue

        # Gemini remediation output: treat as "resolved" stage.
        if msg.startswith("GEMINI_OUTPUT:"):
            output = msg[len("GEMINI_OUTPUT:") :].strip()
            fault_code = None
            if request_id:
                fault_code = last_fault_by_request.get(request_id)
            if not fault_code:
                # As a last resort, scan the output for a fault code.
                fault_code = _extract_fault_code_any(output, allowed_faults)
            if not fault_code:
                continue

            inc = _get_or_create_incident(fault_code, ev.timestamp)
            inc["status"] = "resolved"
            inc["timestamp_resolved"] = ev.timestamp.replace(tzinfo=None)
            inc["remediation"] = {
                "action_type": "gemini_autofix",
                "parameters": {"summary": output[:1000]},
                "execution_timestamp": ev.timestamp.replace(tzinfo=None),
            }
            inc["verification"] = {
                "error_rate_before": None,
                "error_rate_after": None,
                "latency_before": None,
                "latency_after": None,
                "health_check_status": "unknown",
                "success": True,
            }
            inc["breadcrumbs"]["recent_logs"].append(
                f"{ev.timestamp.isoformat()} GEMINI_OUTPUT"
            )
            continue

        # Ignore operational dashboard failures when user wants only processed faults.
        if msg.startswith("DASHBOARD "):
            continue

    # Trim logs and compute basic symptoms.
    out: list[dict[str, Any]] = []
    for fault_code, inc in incidents.items():
        logs = inc["breadcrumbs"].get("recent_logs") or []
        inc["breadcrumbs"]["recent_logs"] = logs[-logs_per_incident:]
        inc["symptoms"]["affected_requests"] = max(1, len(logs))
        out.append(inc)

    out.sort(key=lambda i: i["timestamp_opened"], reverse=True)
    return out[:max_incidents]


def _stable_id(*parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode("utf-8"), usedforsecurity=False)
    return h.hexdigest()[:10]


def _guess_severity(error_code: str, message: str) -> str:
    msg = message.lower()
    if "critical" in msg or "panic" in msg:
        return "critical"
    if "traceback" in msg or "exception" in msg:
        return "high"
    if "db" in error_code.lower() or "timeout" in msg:
        return "critical"
    if "sql" in error_code.lower():
        return "high"
    return "medium"


def _guess_type(error_code: str) -> str:
    ec = error_code.upper()
    if "EXTERNAL_API" in ec:
        return "External API Timeout"
    if "DB" in ec:
        return "Database Issues"
    if "SQL" in ec:
        return "SQL Errors"
    if "CONNECTION" in ec:
        return "Connection Errors"
    return "Application Error"


def _extract_error_code(message: str) -> str:
    only_fault_codes = os.getenv("CLOUDWATCH_ONLY_FAULT_CODES", "true").lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    )
    if only_fault_codes:
        allowed = get_fault_codes()
        fc = _extract_fault_code_any(message, allowed)
        if fc:
            return fc

    m = _DASHBOARD_RE.match(message)
    if m:
        # Examples:
        # - DASHBOARD emit failed: <HTTPError 405: 'METHOD NOT ALLOWED'>
        # - DASHBOARD create incident failed: <HTTPError 405: 'METHOD NOT ALLOWED'>
        action = re.sub(r"\W+", "_", m.group(1).strip()).upper()
        return f"DASHBOARD_{action}_FAILED"

    m = _FAULT_RE.search(message)
    if m:
        return m.group(1)

    # Lambda-style JSON blobs (e.g., BACKBOARD_ANALYSIS: {...})
    if "{" in message and "}" in message:
        try:
            payload = message[message.index("{") : message.rindex("}") + 1]
            obj = json.loads(payload)
            ec = obj.get("error_code") or obj.get("code")
            if isinstance(ec, str) and ec:
                return ec
        except Exception:
            pass

    if "traceback" in message.lower() or "exception" in message.lower():
        return "PY_EXCEPTION"

    return "ERROR"


def _extract_route(message: str) -> str | None:
    m = _ROUTE_RE.search(message)
    if m:
        return m.group(1)

    # Infer the target endpoint from FaultRouter dashboard actions.
    if message.startswith("DASHBOARD emit failed"):
        return "/incidents/stream"
    if message.startswith("DASHBOARD create incident failed"):
        return "/incidents/"

    return None


def _extract_latency_s(message: str) -> float | None:
    m = _LATENCY_RE.search(message)
    return float(m.group(1)) if m else None


def build_incidents_from_events(
    events: list[CloudWatchLogEvent],
    *,
    max_incidents: int = 50,
    logs_per_incident: int = 8,
) -> list[dict[str, Any]]:
    """Convert log events into incident-shaped dicts for the dev dashboard."""
    if not events:
        return []

    # Keep only relevant events and drop common Lambda noise.
    # When CLOUDWATCH_ONLY_FAULT_CODES=true, only include the exact FAULT codes
    # the FaultRouter Lambda processes.
    noise_prefixes = (
        "INIT_START",
        "START RequestId:",
        "END RequestId:",
        "REPORT RequestId:",
        "BACKBOARD_ANALYSIS:",
        "TOOL_CALL:",
        "TOOL_RESULT:",
        "GEMINI_OUTPUT:",
    )

    only_fault_codes = os.getenv("CLOUDWATCH_ONLY_FAULT_CODES", "true").lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    )
    allowed_faults = get_fault_codes()

    filtered: list[CloudWatchLogEvent] = []
    for ev in events:
        msg = (ev.message or "").strip()
        if not msg:
            continue
        if msg.startswith(noise_prefixes):
            continue

        if only_fault_codes:
            # Strict mode: only include the fault codes FaultRouter processes.
            if _extract_fault_code_any(msg, allowed_faults):
                filtered.append(ev)
            continue

        has_fault = "FAULT_" in msg
        has_route = "route=" in msg
        has_error_word = "ERROR" in msg
        has_traceback = "Traceback" in msg or "Exception" in msg
        is_dashboard_failure = msg.startswith("DASHBOARD ") and "failed" in msg.lower()

        if (has_fault and has_route) or is_dashboard_failure or has_error_word or has_traceback:
            filtered.append(ev)

    # Group by (error_code, route) so repeated lines collapse into a single incident.
    buckets: dict[tuple[str, str], list[CloudWatchLogEvent]] = {}
    for ev in filtered:
        error_code = _extract_error_code(ev.message)
        route = _extract_route(ev.message) or "-"
        buckets.setdefault((error_code, route), []).append(ev)

    # Sort buckets by most recent event.
    bucket_items = sorted(
        buckets.items(),
        key=lambda item: max(e.timestamp_ms for e in item[1]),
        reverse=True,
    )

    incidents: list[dict[str, Any]] = []
    for (error_code, route), evs in bucket_items[:max_incidents]:
        evs.sort(key=lambda e: e.timestamp_ms, reverse=True)
        newest = evs[0]
        count = len(evs)
        latency = None
        for e in evs:
            latency = _extract_latency_s(e.message)
            if latency is not None:
                break

        severity = _guess_severity(error_code, newest.message)
        incident_type = _guess_type(error_code)
        incident_id = f"CW-{_stable_id(error_code, route, str(newest.timestamp_ms))}"

        error_rate_value = min(100, max(1, count * 5))
        error_rate = f"{error_rate_value}%"

        incidents.append(
            {
                "id": incident_id,
                "timestamp_opened": newest.timestamp.replace(tzinfo=None),
                "timestamp_resolved": None,
                "incident_type": incident_type,
                "severity": severity,
                "status": "detected",
                "route": route,
                "error_code": error_code,
                "symptoms": {
                    "error_rate": error_rate,
                    "error_rate_value": error_rate_value,
                    "latency_p95": (
                        f"{latency:.2f}s" if latency is not None else "—"
                    ),
                    "latency_p95_value": (
                        float(latency) if latency is not None else 0.0
                    ),
                    "endpoint": route,
                    "log_marker": error_code,
                    "affected_requests": count,
                },
                "breadcrumbs": {
                    "recent_logs": [
                        f"{e.timestamp.isoformat()} {e.message}"
                        for e in evs[:logs_per_incident]
                    ],
                    "metric_snapshot": {
                        "total_requests": None,
                        "failed_requests": count,
                        "avg_latency": (
                            f"{latency:.2f}s" if latency is not None else None
                        ),
                        "timestamp": newest.timestamp.isoformat(),
                    },
                    "correlated_events": [
                        f"log_group={newest.log_group}",
                        f"log_stream={newest.log_stream}" if newest.log_stream else None,
                    ],
                },
                "root_cause": {
                    "source": "cloudwatch",
                    "confidence_score": None,
                    "explanation": "Pending RAG analysis (log-derived incident)",
                },
                "remediation": {
                    "action_type": None,
                    "parameters": None,
                    "execution_timestamp": None,
                },
                "verification": {
                    "error_rate_before": None,
                    "error_rate_after": None,
                    "latency_before": None,
                    "latency_after": None,
                    "health_check_status": None,
                    "success": None,
                },
            }
        )

    # Clean correlated_events Nones.
    for inc in incidents:
        inc["breadcrumbs"]["correlated_events"] = [
            e for e in inc["breadcrumbs"]["correlated_events"] if e
        ]

    return incidents
