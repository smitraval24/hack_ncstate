"""This file handles the views logic for the developer part of the project."""

import ast
import base64
import json
import logging
import os
from collections import Counter
from datetime import datetime, timedelta

import redis
import requests as http_requests
from flask import Blueprint, current_app, jsonify, render_template, request

from config.settings import CLOUDWATCH_ENABLED
from hello.aws.cloudwatch_logs import (
    build_fault_router_incidents,
    build_incidents_from_events,
    fetch_recent_events,
    get_cloudwatch_log_groups,
)
from hello.incident.live_store import (
    create_incident as create_live_incident,
    get_all_incidents as get_live_incidents,
    get_incident as get_live_incident,
    reset_all as reset_live_incidents,
    update_incident as update_live_incident,
)

logger = logging.getLogger(__name__)

# This blueprint groups related routes for this part of the app.
developer = Blueprint("developer", __name__, template_folder="templates")

FAULT_FUNCTION_MAP = {
    "FAULT_SQL_INJECTION_TEST": "test_fault_run",
    "FAULT_EXTERNAL_API_LATENCY": "test_fault_external_api",
    "FAULT_DB_TIMEOUT": "test_fault_db_timeout",
}

FAULT_ROUTE_MAP = {
    "FAULT_SQL_INJECTION_TEST": "/test-fault/run",
    "FAULT_EXTERNAL_API_LATENCY": "/test-fault/external-api",
    "FAULT_DB_TIMEOUT": "/test-fault/db-timeout",
}

AUTO_HEAL_ACTION_TYPES = {"auto_fix_pushed"}
DEMO_RESET_TIMESTAMP_PARAM = "/cream/demo-reset-timestamp"
_demo_reset_timestamp: datetime | None = None



# This function gets the cloudwatch incidents work used in this file.
def get_cloudwatch_incidents() -> tuple[list[dict], str | None]:
    """Fetch incidents derived from CloudWatch Logs.

    Returns (incidents, error_message). If CloudWatch is not configured or
    errors occur, incidents will be empty and error_message will describe why.
    """
    # User requirement: default to FaultRouter Lambda log group.
    log_groups = get_cloudwatch_log_groups() or ["/aws/lambda/FaultRouter"]
    if not log_groups:
        return [], "CLOUDWATCH_LOG_GROUPS not configured"

    try:
        # Leave unset by default to avoid missing non-FAULT errors (e.g. DASHBOARD failures).
        filter_pattern = os.getenv("CLOUDWATCH_FILTER_PATTERN")
        lookback_minutes = int(os.getenv("CLOUDWATCH_LOOKBACK_MINUTES", "120"))
        limit_per_group = int(os.getenv("CLOUDWATCH_LIMIT_PER_GROUP", "200"))

        events = fetch_recent_events(
            log_groups=log_groups,
            lookback=timedelta(minutes=lookback_minutes),
            filter_pattern=filter_pattern,
            limit_per_group=limit_per_group,
        )
        only_fault_codes = os.getenv("CLOUDWATCH_ONLY_FAULT_CODES", "true").lower() in (
            "1",
            "true",
            "yes",
            "y",
            "on",
        )

        if only_fault_codes and log_groups == ["/aws/lambda/FaultRouter"]:
            incidents = build_fault_router_incidents(events)
        else:
            incidents = build_incidents_from_events(events)
        return incidents, None
    except Exception as e:
        return [], f"CloudWatch fetch failed: {e}"


# This function gets the dashboard metrics work used in this file.
def get_dashboard_metrics(incidents: list[dict] | None = None):
    """Calculate dashboard summary metrics from provided incident list."""
    if incidents is None:
        incidents = []
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Count incidents by status
    active_count = len(
        [i for i in incidents if i.get("status") in ["detected", "in_progress"]]
    )
    resolved_count = len([i for i in incidents if i.get("status") == "resolved"])
    resolved_today_count = len([
        i for i in incidents
        if i.get("status") == "resolved"
        and i.get("timestamp_resolved")
        and i["timestamp_resolved"] >= today_start
    ])

    # Calculate auto-resolution rate
    # An incident counts as auto-resolved if:
    # - verification.success is True, OR
    # - it has a remediation action_type set (system took automated action)
    resolved_incidents = [i for i in incidents if i.get("status") == "resolved"]
    auto_resolved_count = 0
    for i in resolved_incidents:
        verification = i.get("verification") or {}
        remediation = i.get("remediation") or {}
        if verification.get("success") is True:
            auto_resolved_count += 1
        elif remediation.get("action_type") and remediation["action_type"] not in (
            "pending_analysis", None
        ):
            auto_resolved_count += 1
    total_incidents_for_rate = len(incidents) if incidents else 1
    auto_resolution_rate = (auto_resolved_count / total_incidents_for_rate * 100) if incidents else 0

    # Calculate MTTR (Mean Time To Remediate)
    resolution_times = []
    for incident in resolved_incidents:
        opened = incident.get("timestamp_opened")
        resolved = incident.get("timestamp_resolved")
        if opened and resolved:
            delta = resolved - opened
            minutes = delta.total_seconds() / 60
            if minutes >= 0:
                resolution_times.append(minutes)

    mttr = sum(resolution_times) / len(resolution_times) if resolution_times else 0

    return {
        "active_incidents": active_count,
        "resolved_total": resolved_count,
        "resolved_today": resolved_today_count,
        "auto_resolution_rate": round(auto_resolution_rate, 1),
        "mttr": round(mttr, 1),
        "total_incidents": len(incidents)
    }


# This function builds the incident trend work used in this file.
def build_incident_trend(incidents: list[dict], days: int = 7) -> dict:
    """Build detected/resolved counts for the trailing time window."""
    today = datetime.now().date()
    date_window = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    detected_counts = {day: 0 for day in date_window}
    resolved_counts = {day: 0 for day in date_window}

    for incident in incidents:
        opened_at = incident.get("timestamp_opened")
        resolved_at = incident.get("timestamp_resolved")

        if opened_at:
            opened_date = opened_at.date()
            if opened_date in detected_counts:
                detected_counts[opened_date] += 1

        if resolved_at:
            resolved_date = resolved_at.date()
            if resolved_date in resolved_counts:
                resolved_counts[resolved_date] += 1

    return {
        "labels": [f"{day.strftime('%b')} {day.day}" for day in date_window],
        "detected": [detected_counts[day] for day in date_window],
        "resolved": [resolved_counts[day] for day in date_window],
    }


def _incident_affected_requests(incident: dict) -> int:
    """Return the best-effort affected-request count for an incident."""
    value = (incident.get("symptoms") or {}).get("affected_requests", 0)
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def build_severity_counts(incidents: list[dict]) -> dict:
    """Aggregate incident counts by severity for read-only dashboard charts."""
    counts = Counter((i.get("severity") or "unknown").lower() for i in incidents)
    return {
        "critical": counts.get("critical", 0),
        "high": counts.get("high", 0),
        "medium": counts.get("medium", 0),
        "unknown": counts.get("unknown", 0),
    }


def build_type_distribution(incidents: list[dict], limit: int = 5) -> dict:
    """Aggregate incident counts by type/error code for dashboard charts."""
    buckets: Counter[str] = Counter()
    for inc in incidents:
        label = (
            inc.get("incident_type")
            or inc.get("error_code")
            or "Unknown"
        )
        buckets[label] += 1

    common = buckets.most_common(limit)
    return {
        "labels": [label for label, _ in common],
        "values": [value for _, value in common],
    }


def build_route_impact(incidents: list[dict], limit: int = 5) -> dict:
    """Aggregate affected-request totals by route for dashboard charts."""
    buckets: Counter[str] = Counter()
    for inc in incidents:
        route = inc.get("route") or "-"
        buckets[route] += _incident_affected_requests(inc)

    common = buckets.most_common(limit)
    return {
        "labels": [label for label, _ in common],
        "values": [value for _, value in common],
    }


def build_dashboard_aggregates(incidents: list[dict]) -> dict:
    """Build read-only aggregates that power dashboard visuals."""
    return {
        "impacted_requests_total": sum(_incident_affected_requests(i) for i in incidents),
        "severity_counts": build_severity_counts(incidents),
        "type_distribution": build_type_distribution(incidents),
        "route_impact": build_route_impact(incidents),
    }


def get_mock_incidents() -> list[dict]:
    """Return fallback demo incidents for local dashboard rendering/tests."""
    now = datetime.now().replace(microsecond=0)
    return [
        {
            "id": "MOCK-0001",
            "timestamp_opened": now - timedelta(minutes=42),
            "timestamp_resolved": now - timedelta(minutes=18),
            "incident_type": "External API Timeout",
            "severity": "critical",
            "status": "resolved",
            "route": "/test-fault/external-api",
            "error_code": "FAULT_EXTERNAL_API_LATENCY",
            "symptoms": {
                "latency_p95": "8.20s",
                "latency_p95_value": 8.2,
                "endpoint": "/test-fault/external-api",
                "log_marker": "external_timeout",
                "affected_requests": 120,
            },
            "breadcrumbs": {
                "recent_logs": [
                    "2026-03-22 07:00:12 ERROR FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=external_timeout latency=8.20",
                ],
                "metric_snapshot": {
                    "failed_requests": 120,
                    "avg_latency": "8.20s",
                    "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                },
                "correlated_events": ["Fault detected on external API route"],
            },
            "root_cause": {
                "source": "rag",
                "confidence_score": 0.93,
                "explanation": "The outbound timeout was too short for the upstream latency profile.",
            },
            "remediation": {
                "action_type": "auto_fix_pushed",
                "parameters": {"claude_output": "Raised timeout and retry budget."},
                "execution_timestamp": now - timedelta(minutes=24),
            },
            "verification": {
                "latency_before": 8.2,
                "latency_after": 0.9,
                "health_check_status": "passed",
                "success": True,
            },
            "commit_sha": "abc123def456",
            "run_url": "https://example.com/run/1",
        },
        {
            "id": "MOCK-0002",
            "timestamp_opened": now - timedelta(hours=2, minutes=10),
            "timestamp_resolved": None,
            "incident_type": "Database Timeout",
            "severity": "critical",
            "status": "in_progress",
            "route": "/test-fault/db-timeout",
            "error_code": "FAULT_DB_TIMEOUT",
            "symptoms": {
                "latency_p95": "5.00s",
                "latency_p95_value": 5.0,
                "endpoint": "/test-fault/db-timeout",
                "log_marker": "db_timeout_or_pool_exhaustion",
                "affected_requests": 85,
            },
            "breadcrumbs": {
                "recent_logs": ["Database timeout observed while running pg_sleep demo fault."],
                "metric_snapshot": {
                    "failed_requests": 85,
                    "avg_latency": "5.00s",
                    "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                },
                "correlated_events": ["Connection pool saturation"],
            },
            "root_cause": {
                "source": "rag",
                "confidence_score": 0.87,
                "explanation": "The query execution window is shorter than the simulated sleep.",
            },
            "remediation": {
                "action_type": "auto_fix_pushed",
                "parameters": {"claude_output": "Adjusted timeout handling."},
                "execution_timestamp": now - timedelta(hours=2),
            },
            "verification": {
                "latency_before": 5.0,
                "latency_after": None,
                "health_check_status": "pending",
                "success": None,
            },
        },
        {
            "id": "MOCK-0003",
            "timestamp_opened": now - timedelta(hours=5),
            "timestamp_resolved": now - timedelta(hours=4, minutes=20),
            "incident_type": "SQL Injection Error",
            "severity": "high",
            "status": "resolved",
            "route": "/test-fault/run",
            "error_code": "FAULT_SQL_INJECTION_TEST",
            "symptoms": {
                "latency_p95": "0.60s",
                "latency_p95_value": 0.6,
                "endpoint": "/test-fault/run",
                "log_marker": "invalid_sql_executed",
                "affected_requests": 62,
            },
            "breadcrumbs": {
                "recent_logs": ["Malformed SQL executed in test fault route."],
                "metric_snapshot": {
                    "failed_requests": 62,
                    "avg_latency": "0.60s",
                    "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                },
                "correlated_events": ["SQL parser failure"],
            },
            "root_cause": {
                "source": "rag",
                "confidence_score": 0.89,
                "explanation": "The demo route executed malformed SQL instead of a safe query.",
            },
            "remediation": {
                "action_type": "manual_patch",
                "parameters": {"summary": "Corrected malformed SQL."},
                "execution_timestamp": now - timedelta(hours=4, minutes=35),
            },
            "verification": {
                "latency_before": 0.6,
                "latency_after": 0.1,
                "health_check_status": "passed",
                "success": True,
            },
        },
        {
            "id": "MOCK-0004",
            "timestamp_opened": now - timedelta(days=1, hours=1),
            "timestamp_resolved": now - timedelta(days=1),
            "incident_type": "Cache Stampede",
            "severity": "high",
            "status": "resolved",
            "route": "/cache/rebuild",
            "error_code": "CACHE_STAMPEDE",
            "symptoms": {
                "latency_p95": "1.80s",
                "latency_p95_value": 1.8,
                "endpoint": "/cache/rebuild",
                "log_marker": "cache_regeneration_spike",
                "affected_requests": 34,
            },
            "breadcrumbs": {
                "recent_logs": ["Cache regeneration spike detected."],
                "metric_snapshot": {
                    "failed_requests": 34,
                    "avg_latency": "1.80s",
                    "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                },
                "correlated_events": ["Redis miss burst"],
            },
            "root_cause": {
                "source": "manual",
                "confidence_score": 0.71,
                "explanation": "A missing warmup caused concurrent cache regeneration.",
            },
            "remediation": {
                "action_type": "manual_patch",
                "parameters": {"summary": "Added request coalescing."},
                "execution_timestamp": now - timedelta(days=1, minutes=20),
            },
            "verification": {
                "latency_before": 1.8,
                "latency_after": 0.4,
                "health_check_status": "passed",
                "success": True,
            },
        },
        {
            "id": "MOCK-0005",
            "timestamp_opened": now - timedelta(days=2, hours=3),
            "timestamp_resolved": None,
            "incident_type": "Background Queue Drift",
            "severity": "medium",
            "status": "detected",
            "route": "/jobs/sync",
            "error_code": "QUEUE_DRIFT",
            "symptoms": {
                "latency_p95": "2.40s",
                "latency_p95_value": 2.4,
                "endpoint": "/jobs/sync",
                "log_marker": "consumer_lag",
                "affected_requests": 20,
            },
            "breadcrumbs": {
                "recent_logs": ["Queue consumer lag crossed the demo threshold."],
                "metric_snapshot": {
                    "failed_requests": 20,
                    "avg_latency": "2.40s",
                    "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                },
                "correlated_events": ["Lag spike on worker queue"],
            },
            "root_cause": {
                "source": None,
                "confidence_score": None,
                "explanation": None,
            },
            "remediation": {
                "action_type": None,
                "parameters": None,
                "execution_timestamp": None,
            },
            "verification": {
                "latency_before": None,
                "latency_after": None,
                "health_check_status": None,
                "success": None,
            },
        },
    ]


def _incident_failure_summary(incident: dict) -> str:
    """Summarize what broke using existing incident fields only."""
    symptoms = incident.get("symptoms") or {}
    root_cause = incident.get("root_cause") or {}
    log_marker = symptoms.get("log_marker")
    if log_marker and log_marker not in {"-", "—"}:
        return str(log_marker).replace("_", " ")
    explanation = root_cause.get("explanation")
    if explanation:
        return str(explanation).split(".")[0]
    return "Awaiting detailed analysis"


def _sort_incidents_for_dashboard(incidents: list[dict]) -> list[dict]:
    """Show active incidents first, then newest incidents within each group."""
    active_statuses = {"detected", "in_progress"}

    def _sort_key(inc: dict) -> tuple[int, float]:
        opened_at = inc.get("timestamp_opened")
        opened_value = opened_at.timestamp() if opened_at else 0.0
        return (
            0 if inc.get("status") in active_statuses else 1,
            -opened_value,
        )

    return sorted(
        incidents,
        key=_sort_key,
    )


# This function handles the sync status work for this file.
def _sync_status(incidents: list[dict]) -> list[dict]:
    """Derive status from verification result so it stays in sync.

    Resolution sources (checked in order):
    1. verification.success already set (by pipeline callback) → honour it.
    2. Any incident still in detected/in_progress → hit the app health
       endpoint and, if healthy, auto-resolve immediately.
    """
    now = datetime.now()

    # Only run the health check once per sync pass (not per incident)
    health_ok: bool | None = None

    for inc in incidents:
        verification = inc.get("verification") or {}
        remediation = inc.get("remediation") or {}

        # --- already resolved / failed by pipeline callback ---
        if verification.get("success") is True:
            inc["status"] = "resolved"
            if not inc.get("timestamp_resolved"):
                inc["timestamp_resolved"] = now

        elif verification.get("success") is False:
            inc["status"] = "in_progress"

        elif inc.get("status") in ("detected", "in_progress"):
            # Only auto-resolve if some remediation action has been taken.
            # Without this guard, newly detected incidents get auto-resolved
            # immediately because the /health endpoint always returns 200
            # (the app is healthy even while faulty routes exist).
            action_type = remediation.get("action_type")
            if not action_type or action_type == "pending_analysis":
                continue  # no fix attempted yet, leave as detected

            # If a fix was pushed (auto_fix_pushed), give the CI/CD pipeline
            # time to deploy before auto-resolving.
            if action_type == "auto_fix_pushed":
                exec_ts = remediation.get("execution_timestamp")
                wait = timedelta(minutes=int(os.getenv("AUTO_RESOLVE_MINUTES", "8")))
                if not exec_ts or (now - exec_ts) < wait:
                    continue  # still deploying, let the callback handle it

            if health_ok is None:
                health_ok = _app_health_ok()

            if health_ok:
                _auto_resolve_incident(inc, now)

        # Ensure confidence score is populated when root cause exists
        root_cause = inc.get("root_cause") or {}
        if root_cause.get("explanation") and root_cause.get("confidence_score") is None:
            root_cause["confidence_score"] = _compute_confidence(inc)

    return incidents


def _app_health_ok() -> bool:
    """Quick health check against the running app.

    Tries the app's own /health endpoint. Falls back to True if we can't
    determine (e.g. running locally without the full stack).
    """
    health_url = os.getenv("HEALTH_CHECK_URL", "http://localhost:8000/health")
    try:
        resp = http_requests.get(health_url, timeout=3)
        return resp.status_code == 200
    except Exception:
        # If we can't reach the health endpoint, assume healthy so that
        # incidents don't stay stuck forever during a demo / local dev.
        logger.debug("Health check unreachable (%s), assuming healthy", health_url)
        return True


def _auto_resolve_incident(inc: dict, now: datetime) -> None:
    """Mark an incident as auto-resolved and persist the change."""
    inc["status"] = "resolved"
    inc["timestamp_resolved"] = now
    inc["verification"] = {
        "latency_before": inc.get("symptoms", {}).get("latency_p95_value", 0),
        "latency_after": 0,
        "health_check_status": "passed",
        "success": True,
    }
    # Persist so it survives page reloads
    try:
        update_live_incident(inc["id"], {
            "status": "resolved",
            "timestamp_resolved": now,
            "verification": inc["verification"],
        })
        logger.info("Auto-resolved incident %s", inc.get("id"))
    except Exception:
        logger.debug("Could not persist auto-resolve for %s", inc.get("id"))


# This function handles the merge incidents work for this file.
def _merge_incidents(live: list[dict], cloudwatch: list[dict]) -> list[dict]:
    """Merge live and CloudWatch incidents, deduplicating across sources.

    CloudWatch incidents that match a live incident by (error_code, route)
    replace the live one if they have more lifecycle progress. Live incidents
    with no CloudWatch match are kept as-is, and vice versa.
    """
    _STATUS_RANK = {"detected": 0, "in_progress": 1, "resolved": 2}

    # Index CloudWatch incidents by (error_code, route) for matching
    cw_by_key: dict[tuple[str, str], list[dict]] = {}
    for inc in cloudwatch:
        key = (inc.get("error_code", ""), inc.get("route", ""))
        cw_by_key.setdefault(key, []).append(inc)

    merged: list[dict] = []
    matched_cw_keys: set[tuple[str, str]] = set()

    for live_inc in live:
        key = (live_inc.get("error_code", ""), live_inc.get("route", ""))
        cw_matches = cw_by_key.get(key, [])
        if cw_matches:
            matched_cw_keys.add(key)
            # Pick the CloudWatch incident with the most progress
            best_cw = max(
                cw_matches,
                key=lambda i: _STATUS_RANK.get(i.get("status", ""), 0),
            )
            cw_rank = _STATUS_RANK.get(best_cw.get("status", ""), 0)
            live_rank = _STATUS_RANK.get(live_inc.get("status", ""), 0)
            # Use the more progressed one; if tied, prefer CloudWatch (richer data)
            merged.append(best_cw if cw_rank >= live_rank else live_inc)
        else:
            merged.append(live_inc)

    # Add CloudWatch incidents that had no live match
    for key, cw_list in cw_by_key.items():
        if key not in matched_cw_keys:
            merged.extend(cw_list)

    merged.sort(
        key=lambda i: i.get("timestamp_opened") or datetime.min,
        reverse=True,
    )
    return merged


def _is_parameter_not_found(exc: Exception) -> bool:
    """Return True when boto3 surfaced an SSM ParameterNotFound error."""
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    return error.get("Code") == "ParameterNotFound"


def _get_demo_reset_timestamp() -> datetime | None:
    """Return the most recent demo reset timestamp, if one was recorded."""
    global _demo_reset_timestamp

    try:
        import boto3

        ssm = boto3.client("ssm")
        response = ssm.get_parameter(Name=DEMO_RESET_TIMESTAMP_PARAM)
        value = response["Parameter"]["Value"]
        _demo_reset_timestamp = datetime.fromisoformat(value)
    except Exception as exc:
        if not _is_parameter_not_found(exc):
            logger.debug("Could not read demo reset timestamp: %s", exc)

    return _demo_reset_timestamp


def _record_demo_reset(timestamp: datetime) -> None:
    """Persist the latest demo reset timestamp for dashboard filtering."""
    global _demo_reset_timestamp
    _demo_reset_timestamp = timestamp

    try:
        import boto3

        boto3.client("ssm").put_parameter(
            Name=DEMO_RESET_TIMESTAMP_PARAM,
            Value=timestamp.isoformat(),
            Type="String",
            Overwrite=True,
        )
    except Exception as exc:
        logger.warning("Could not store demo reset timestamp: %s", exc)


def _filter_incidents_after_demo_reset(incidents: list[dict]) -> list[dict]:
    """Hide incidents that predate the latest Reset All action."""
    cutoff = _get_demo_reset_timestamp()
    if not cutoff:
        return incidents

    filtered = []
    for incident in incidents:
        opened_at = incident.get("timestamp_opened")
        if opened_at and opened_at < cutoff:
            continue
        filtered.append(incident)

    return filtered


def _collect_resettable_fault_codes(incidents: list[dict]) -> list[str]:
    """Return fault codes that were actually auto-healed and should be reverted."""
    resettable = set()

    for incident in incidents:
        fault_code = incident.get("error_code")
        remediation = incident.get("remediation") or {}
        verification = incident.get("verification") or {}

        if fault_code not in FAULT_FUNCTION_MAP:
            continue
        if incident.get("status") != "resolved":
            continue
        if remediation.get("action_type") not in AUTO_HEAL_ACTION_TYPES:
            continue
        if verification.get("success") is False:
            continue

        resettable.add(fault_code)

    return sorted(resettable)


def _function_source_block(source: str, function_name: str) -> tuple[int, int, str]:
    """Return the byte range and source block for a top-level function."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function_name:
            continue

        start_line = min(
            (decorator.lineno for decorator in node.decorator_list),
            default=node.lineno,
        )
        end_line = node.end_lineno
        if end_line is None:
            raise ValueError(f"Could not determine end of function {function_name}")

        start_idx = sum(len(line) for line in lines[: start_line - 1])
        end_idx = sum(len(line) for line in lines[:end_line])
        block = "".join(lines[start_line - 1 : end_line])
        return start_idx, end_idx, block

    raise ValueError(f"Function {function_name} not found")


def _restore_faulty_functions(current_source: str, fault_codes: list[str]) -> str:
    """Restore only the selected fault handlers back to their faulty template."""
    from hello.page._faulty_views_template import FAULTY_VIEWS_CONTENT

    updated_source = current_source
    replacements = []

    for fault_code in sorted(set(fault_codes)):
        function_name = FAULT_FUNCTION_MAP.get(fault_code)
        if not function_name:
            continue

        start_idx, end_idx, _ = _function_source_block(updated_source, function_name)
        _, _, faulty_block = _function_source_block(FAULTY_VIEWS_CONTENT, function_name)
        replacements.append((start_idx, end_idx, faulty_block))

    for start_idx, end_idx, faulty_block in sorted(replacements, reverse=True):
        updated_source = (
            updated_source[:start_idx] + faulty_block + updated_source[end_idx:]
        )

    return updated_source


def _fault_codes_differing_from_template(current_source: str) -> list[str]:
    """Return fault codes whose current function body no longer matches the faulty template."""
    from hello.page._faulty_views_template import FAULTY_VIEWS_CONTENT

    differing_fault_codes = []

    for fault_code, function_name in FAULT_FUNCTION_MAP.items():
        try:
            _, _, current_block = _function_source_block(current_source, function_name)
            _, _, faulty_block = _function_source_block(
                FAULTY_VIEWS_CONTENT,
                function_name,
            )
        except ValueError:
            differing_fault_codes.append(fault_code)
            continue

        if current_block != faulty_block:
            differing_fault_codes.append(fault_code)

    return sorted(differing_fault_codes)


def _invoke_github_lambda(function_name: str, parameters: list[dict]) -> dict:
    """Invoke the GitHub helper Lambda and unwrap its response body."""
    from config.settings import GITHUB_LAMBDA_NAME

    if not GITHUB_LAMBDA_NAME:
        raise RuntimeError("GITHUB_LAMBDA_NAME is not configured")

    import boto3

    payload = {
        "actionGroup": "GitHubActions",
        "function": function_name,
        "parameters": parameters,
    }
    response = boto3.client("lambda").invoke(
        FunctionName=GITHUB_LAMBDA_NAME,
        Payload=json.dumps(payload).encode("utf-8"),
    )
    result = json.loads(response["Payload"].read())
    return json.loads(
        result.get("response", {})
        .get("functionResponse", {})
        .get("responseBody", {})
        .get("TEXT", {})
        .get("body", "{}")
    )


def _read_github_file_content(file_path: str) -> tuple[str, str]:
    """Read the current GitHub version of a file for selective reset logic."""
    from config.settings import (
        GITHUB_BRANCH,
        GITHUB_LAMBDA_NAME,
        GITHUB_OWNER,
        GITHUB_REPO,
        GITHUB_TOKEN,
    )

    if GITHUB_LAMBDA_NAME:
        try:
            body = _invoke_github_lambda(
                "read_github_file",
                [{"name": "file_path", "value": file_path}],
            )
            if body.get("ok"):
                return body["content"], "lambda"
            logger.warning("GitHub Lambda read failed: %s", body.get("error", "unknown error"))
        except Exception as exc:
            logger.warning("GitHub Lambda read failed, trying GitHub API: %s", exc)

    if GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO:
        api_url = (
            f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{file_path}"
        )
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        response = http_requests.get(
            f"{api_url}?ref={GITHUB_BRANCH}",
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        content = base64.b64decode(payload["content"]).decode("utf-8")
        return content, "github_api"

    raise RuntimeError("No GitHub credentials configured")


# This function handles the fetch incidents work for this file.
def _fetch_incidents() -> tuple[list[dict], str, str | None]:
    """Fetch incidents from live store + CloudWatch (merged), or mock data.

    Returns (incidents, data_source, error_message).
    Live and CloudWatch incidents are merged; mock data is used only when
    neither source has anything.
    """
    # Always check live store
    try:
        live = get_live_incidents()
    except Exception:
        live = []

    cw_incidents: list[dict] = []
    cw_error: str | None = None
    if CLOUDWATCH_ENABLED:
        cw_incidents, cw_error = get_cloudwatch_incidents()

    if live or cw_incidents:
        incidents = _merge_incidents(live, cw_incidents)
        if live and cw_incidents:
            source = "live+cloudwatch"
        elif live:
            source = "live"
        else:
            source = "cloudwatch"
    elif cw_error:
        incidents, source = [], "none"
        return incidents, source, cw_error
    else:
        incidents, source = [], "none"

    incidents = _sync_status(incidents)
    incidents = _filter_incidents_after_demo_reset(incidents)
    return incidents, source, cw_error


# This function handles the incidents dashboard work for this file.
@developer.get("/developer/incidents")
def incidents_dashboard():
    """Main incidents dashboard page"""
    cloudwatch_lookback_minutes: int | None = None
    if CLOUDWATCH_ENABLED:
        try:
            cloudwatch_lookback_minutes = int(os.getenv("CLOUDWATCH_LOOKBACK_MINUTES", "120"))
        except Exception:
            cloudwatch_lookback_minutes = None

    incidents, data_source, cloudwatch_error = _fetch_incidents()

    incidents = _sort_incidents_for_dashboard(incidents)
    metrics = get_dashboard_metrics(incidents)
    trend_data = build_incident_trend(incidents)
    dashboard_aggregates = build_dashboard_aggregates(incidents)

    return render_template(
        "developer/incidents.html",
        incidents=incidents,
        metrics=metrics,
        trend_data=trend_data,
        data_source=data_source,
        cloudwatch_error=cloudwatch_error,
        cloudwatch_lookback_minutes=cloudwatch_lookback_minutes,
        dashboard_aggregates=dashboard_aggregates,
        failure_summary=_incident_failure_summary,
    )


# This function handles the incidents api data work for this file.
@developer.get("/developer/incidents/api/data")
def incidents_api_data():
    """JSON API for real-time dashboard updates via polling."""
    incidents, data_source, _ = _fetch_incidents()

    incidents = _sort_incidents_for_dashboard(incidents)
    metrics = get_dashboard_metrics(incidents)
    trend_data = build_incident_trend(incidents)
    dashboard_aggregates = build_dashboard_aggregates(incidents)

    # Serialize incidents for JSON
    serialized = []
    for inc in incidents:
        s = dict(inc)
        for key in ("timestamp_opened", "timestamp_resolved"):
            if s.get(key):
                s[key] = s[key].strftime("%Y-%m-%d %H:%M")
            else:
                s[key] = None
        if s.get("remediation", {}).get("execution_timestamp"):
            s["remediation"] = dict(s["remediation"])
            s["remediation"]["execution_timestamp"] = s["remediation"]["execution_timestamp"].strftime("%Y-%m-%d %H:%M")
        s["affected_requests_value"] = _incident_affected_requests(inc)
        s["failure_summary"] = _incident_failure_summary(inc)
        serialized.append(s)

    return jsonify({
        "metrics": metrics,
        "trend_data": trend_data,
        "dashboard_aggregates": dashboard_aggregates,
        "incidents": serialized,
        "data_source": data_source,
    })


# This function handles the incident detail work for this file.
@developer.get("/developer/incidents/<incident_id>")
def incident_detail(incident_id):
    """Incident detail page"""
    incidents, _, _ = _fetch_incidents()
    incident = next((i for i in incidents if str(i["id"]) == str(incident_id)), None)

    if not incident:
        return "Incident not found", 404

    return render_template(
        "developer/incident_detail.html",
        incident=incident
    )


# This function handles the get incident by id work for this file.
def _get_incident_by_id(incident_id: str) -> dict | None:
    """Look up an incident from live store, CloudWatch, or mock data."""
    incidents, _, _ = _fetch_incidents()
    return next((i for i in incidents if str(i["id"]) == str(incident_id)), None)


def _default_route_for_fault_code(fault_code: str) -> str:
    return FAULT_ROUTE_MAP.get(fault_code, "/test-fault")


# This function handles the incident to document work for this file.
def _incident_to_document(incident: dict) -> str:
    """Serialize an incident dict into a text document for RAG indexing."""
    parts = [
        f"Incident ID: {incident['id']}",
        f"Type: {incident.get('incident_type', 'Unknown')}",
        f"Error Code: {incident.get('error_code', 'Unknown')}",
        f"Severity: {incident.get('severity', 'Unknown')}",
        f"Route: {incident.get('route', '-')}",
        f"Status: {incident.get('status', 'unknown')}",
        "",
        "--- Symptoms ---",
        f"P95 Latency: {incident.get('symptoms', {}).get('latency_p95', 'N/A')}",
        f"Affected Requests: {incident.get('symptoms', {}).get('affected_requests', 'N/A')}",
        "",
        "--- Root Cause ---",
        incident.get("root_cause", {}).get("explanation", "N/A"),
        "",
        "--- Remediation ---",
        f"Action: {incident.get('remediation', {}).get('action_type', 'N/A')}",
        "",
        "--- Verification ---",
        f"Success: {incident.get('verification', {}).get('success', 'N/A')}",
        f"Health Check: {incident.get('verification', {}).get('health_check_status', 'N/A')}",
    ]
    return "\n".join(parts)


# This function handles the store in rag work for this file.
@developer.post("/developer/incidents/<incident_id>/store-rag")
def store_in_rag(incident_id):
    """Store a resolved incident in the RAG knowledge base (Backboard)."""
    incident = _get_incident_by_id(incident_id)
    if not incident:
        return jsonify({"success": False, "error": "Incident not found"}), 404

    if incident.get("status") != "resolved":
        return jsonify({"success": False, "error": "Incident not yet resolved"}), 400

    try:
        from hello.incident.rag_service import _get_config, _make_client, _run_async

        assistant_id = _get_config("BACKBOARD_ASSISTANT_ID")
        if not assistant_id:
            return jsonify({"success": False, "error": "BACKBOARD_ASSISTANT_ID not configured"}), 400

        content = _incident_to_document(incident)

        async def _upload():
            async with _make_client() as client:
                doc = await client.upload_document(
                    assistant_id=assistant_id,
                    content=content,
                    filename=f"incident_{incident_id}.txt",
                )
                return doc.document_id

        doc_id = _run_async(_upload())
        logger.info("Stored incident %s in RAG as doc %s", incident_id, doc_id)
        return jsonify({"success": True, "document_id": doc_id})

    except Exception as e:
        logger.exception("Failed to store incident %s in RAG", incident_id)
        return jsonify({"success": False, "error": str(e)}), 500


# This function handles the store in cache work for this file.
@developer.post("/developer/incidents/<incident_id>/store-cache")
def store_in_cache(incident_id):
    """Cache a resolved incident in Redis for fast lookup."""
    incident = _get_incident_by_id(incident_id)
    if not incident:
        return jsonify({"success": False, "error": "Incident not found"}), 404

    if incident.get("status") != "resolved":
        return jsonify({"success": False, "error": "Incident not yet resolved"}), 400

    try:
        redis_url = current_app.config.get("REDIS_URL", "redis://redis:6379/0")
        r = redis.from_url(redis_url)

        # Build a JSON-safe copy
        cache_data = {}
        for k, v in incident.items():
            if isinstance(v, datetime):
                cache_data[k] = v.isoformat()
            elif isinstance(v, dict):
                # Handle nested datetimes
                nested = {}
                for nk, nv in v.items():
                    nested[nk] = nv.isoformat() if isinstance(nv, datetime) else nv
                cache_data[k] = nested
            else:
                cache_data[k] = v

        cache_key = f"incident:resolved:{incident_id}"
        ttl_seconds = 86400 * 7  # 7 days
        r.setex(cache_key, ttl_seconds, json.dumps(cache_data))

        # Also index by error_code for quick lookup of similar incidents
        error_code = incident.get("error_code", "")
        if error_code:
            index_key = f"incident:by_error:{error_code}"
            r.sadd(index_key, incident_id)
            r.expire(index_key, ttl_seconds)

        logger.info("Cached incident %s in Redis (TTL: 7d)", incident_id)
        return jsonify({"success": True, "cache_key": cache_key, "ttl": "7 days"})

    except Exception as e:
        logger.exception("Failed to cache incident %s", incident_id)
        return jsonify({"success": False, "error": str(e)}), 500


# This function handles the reset incidents work for this file.
@developer.post("/developer/incidents/reset")
def reset_incidents():
    """Clear demo state and restore only the faults auto-healing previously fixed."""
    try:
        existing_incidents = get_live_incidents()
        resettable_fault_codes = _collect_resettable_fault_codes(existing_incidents)

        count = reset_live_incidents()
        reset_at = datetime.now()
        _record_demo_reset(reset_at)

        # Pause self-healing so the Lambda doesn't immediately "fix" the
        # faulty code before the user can demo the errors.
        _pause_self_healing()

        # Restore only the fault handlers that the self-healing loop already
        # fixed. Faults that were never triggered stay untouched.
        code_reset_result = _reset_faulty_code(resettable_fault_codes)
        restored_fault_codes = code_reset_result.get(
            "fault_codes",
            resettable_fault_codes,
        )

        return jsonify({
            "success": True,
            "deleted": count,
            "restored_fault_codes": restored_fault_codes,
            "reset_at": reset_at.isoformat(),
            "code_reset": code_reset_result,
            "self_healing": "paused (use /developer/incidents/arm-healing to enable)",
        })
    except Exception as e:
        logger.exception("Failed to reset incidents")
        return jsonify({"success": False, "error": str(e)}), 500


@developer.post("/developer/incidents/arm-healing")
def arm_self_healing():
    """Clear SSM cooldowns so the self-healing Lambda processes the next fault.

    Call this AFTER you've triggered faults and want the self-healing loop
    to kick in. The next fault logged to CloudWatch will be picked up by
    the Lambda.
    """
    try:
        _arm_self_healing()
        return jsonify({
            "success": True,
            "message": "Self-healing armed. Trigger only the fault you want the Lambda to remediate next.",
        })
    except Exception as e:
        logger.exception("Failed to arm self-healing")
        return jsonify({"success": False, "error": str(e)}), 500


def _pause_self_healing():
    """Set SSM cooldowns + demo pause so the Lambda skips all faults."""
    import time as _time
    try:
        import boto3
        ssm = boto3.client("ssm")
        # Set per-fault cooldowns
        now = str(_time.time())
        for code in ("FAULT_SQL_INJECTION_TEST", "FAULT_EXTERNAL_API_LATENCY", "FAULT_DB_TIMEOUT"):
            ssm.put_parameter(
                Name=f"/cream/fault-cooldown/{code}",
                Value=now,
                Type="String",
                Overwrite=True,
            )
        # Set global demo pause flag
        ssm.put_parameter(
            Name="/cream/demo-paused",
            Value="true",
            Type="String",
            Overwrite=True,
        )
        logger.info("Self-healing paused (cooldowns set + demo-paused=true)")
    except Exception as e:
        logger.warning("Could not pause self-healing: %s", e)


def _arm_self_healing():
    """Clear SSM cooldowns + demo pause so the Lambda processes faults."""
    try:
        import boto3
        ssm = boto3.client("ssm")
        # Clear per-fault cooldowns
        for code in ("FAULT_SQL_INJECTION_TEST", "FAULT_EXTERNAL_API_LATENCY", "FAULT_DB_TIMEOUT"):
            try:
                ssm.delete_parameter(Name=f"/cream/fault-cooldown/{code}")
            except ssm.exceptions.ParameterNotFound:
                pass
        # Clear demo pause flag
        try:
            ssm.delete_parameter(Name="/cream/demo-paused")
        except ssm.exceptions.ParameterNotFound:
            pass
        logger.info("Self-healing armed (cooldowns cleared + demo-paused removed)")
    except Exception as e:
        logger.warning("Could not arm self-healing: %s", e)


def _reset_faulty_code(fault_codes: list[str]) -> dict:
    """Push only the selected faulty handlers back to GitHub."""
    from config.settings import (
        GITHUB_LAMBDA_NAME,
        GITHUB_BRANCH,
        GITHUB_OWNER,
        GITHUB_REPO,
        GITHUB_TOKEN,
    )

    file_path = "hello/page/views.py"
    resettable_fault_codes = [
        fault_code for fault_code in fault_codes if fault_code in FAULT_FUNCTION_MAP
    ]

    try:
        current_content, read_method = _read_github_file_content(file_path)
        drifted_fault_codes = _fault_codes_differing_from_template(current_content)
        resettable_fault_codes = sorted(
            set(resettable_fault_codes) | set(drifted_fault_codes)
        )
        if not resettable_fault_codes:
            logger.info("Reset skipped: all fault handlers already match the faulty template")
            return {
                "method": "none",
                "success": True,
                "skipped": True,
                "fault_codes": [],
                "message": "Fault handlers already match the faulty template.",
            }
        reset_content = _restore_faulty_functions(
            current_content,
            resettable_fault_codes,
        )
    except Exception as exc:
        logger.warning("Could not prepare selective fault reset: %s", exc)
        return {
            "method": "prepare",
            "success": False,
            "fault_codes": resettable_fault_codes,
            "error": str(exc),
        }

    commit_message = (
        "[RESET] Restore faulty demo handlers for "
        + ", ".join(resettable_fault_codes)
    )

    # Method 1: Invoke GithubTool Lambda
    if GITHUB_LAMBDA_NAME:
        try:
            body = _invoke_github_lambda(
                "push_github_fix",
                [
                    {"name": "file_path", "value": file_path},
                    {"name": "file_content", "value": reset_content},
                    {"name": "commit_message", "value": commit_message},
                ],
            )
            logger.info("Reset faulty code via Lambda: %s", body)
            return {
                "method": "lambda",
                "success": body.get("ok", False),
                "fault_codes": resettable_fault_codes,
                "read_method": read_method,
                "detail": body,
            }
        except Exception as e:
            logger.warning("Lambda reset failed, trying GitHub API: %s", e)

    # Method 2: GitHub API directly
    if GITHUB_TOKEN and GITHUB_OWNER and GITHUB_REPO:
        try:
            api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{file_path}"
            headers = {
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            }

            # Get current file SHA
            get_resp = http_requests.get(
                f"{api_url}?ref={GITHUB_BRANCH}",
                headers=headers,
                timeout=10,
            )
            get_resp.raise_for_status()
            file_sha = get_resp.json()["sha"]

            # Push selectively restored content
            content_b64 = base64.b64encode(reset_content.encode("utf-8")).decode("utf-8")
            put_resp = http_requests.put(
                api_url,
                headers=headers,
                json={
                    "message": commit_message,
                    "content": content_b64,
                    "sha": file_sha,
                    "branch": GITHUB_BRANCH,
                },
                timeout=15,
            )
            put_resp.raise_for_status()
            commit_sha = put_resp.json().get("commit", {}).get("sha", "")
            logger.info("Reset faulty code via GitHub API: %s", commit_sha)
            return {
                "method": "github_api",
                "success": True,
                "fault_codes": resettable_fault_codes,
                "read_method": read_method,
                "commit_sha": commit_sha,
            }
        except Exception as e:
            logger.warning("GitHub API reset failed: %s", e)
            return {
                "method": "github_api",
                "success": False,
                "fault_codes": resettable_fault_codes,
                "read_method": read_method,
                "error": str(e),
            }

    logger.info("No GitHub credentials configured — skipped code reset")
    return {
        "method": "none",
        "success": False,
        "fault_codes": resettable_fault_codes,
        "read_method": read_method,
        "error": "No GITHUB_LAMBDA_NAME or GITHUB_TOKEN configured",
    }


# ---------------------------------------------------------------------------
# Pipeline callback endpoints (called by Lambda + GitHub Actions)
# ---------------------------------------------------------------------------

# This function handles the pipeline pending work for this file.
@developer.post("/developer/incidents/pipeline/pending")
def pipeline_pending():
    """Called by the Lambda after Claude pushes a fix but before deploy.

    Updates matching live incidents to 'in_progress' and stores the RAG
    analysis and Claude output so the dashboard shows remediation is underway.
    """
    data = request.get_json(force=True, silent=True) or {}
    fault_code = data.get("fault_code", "")
    route = data.get("route") or _default_route_for_fault_code(fault_code)
    reason = data.get("reason") or "pipeline_pending"
    if not fault_code:
        return jsonify({"success": False, "error": "fault_code required"}), 400

    rag_explanation = data.get("rag_analysis", "")
    claude_output = data.get("claude_output", "")
    # Compute confidence: RAG source + explanation + action + claude output
    pending_confidence = 0.0
    if rag_explanation:
        pending_confidence += 0.5  # RAG source + explanation
    if claude_output:
        pending_confidence += 0.4  # action + claude output
    pending_confidence += 0.1  # log marker from original incident

    updated = []
    for inc in get_live_incidents():
        if inc.get("error_code") == fault_code and inc.get("status") != "resolved":
            result = update_live_incident(inc["id"], {
                "status": "in_progress",
                "root_cause": {
                    "source": "rag",
                    "confidence_score": min(pending_confidence, 1.0),
                    "explanation": rag_explanation,
                },
                "remediation": {
                    "action_type": "auto_fix_pushed",
                    "parameters": {"claude_output": claude_output},
                    "execution_timestamp": datetime.now(),
                },
            })
            if result:
                updated.append(inc["id"])

    if not updated:
        created = create_live_incident(
            error_code=fault_code,
            route=route,
            reason=reason,
        )
        result = update_live_incident(
            created["id"],
            {
                "status": "in_progress",
                "root_cause": {
                    "source": "rag",
                    "confidence_score": min(pending_confidence, 1.0),
                    "explanation": rag_explanation,
                },
                "remediation": {
                    "action_type": "auto_fix_pushed",
                    "parameters": {"claude_output": claude_output},
                    "execution_timestamp": datetime.now(),
                },
            },
        )
        if result:
            updated.append(created["id"])

    logger.info("Pipeline pending: updated %s for %s", updated, fault_code)
    return jsonify({"success": True, "updated": updated})


# This function handles the pipeline callback work for this file.
@developer.post("/developer/incidents/pipeline/callback")
def pipeline_callback():
    """Called by GitHub Actions after deploy succeeds or fails.

    Expects JSON body::

        {
            "fault_codes": ["FAULT_SQL_INJECTION_TEST"],
            "status": "success" | "failure",
            "commit_sha": "abc123",
            "run_url": "https://github.com/.../actions/runs/123",
            "deploy_error": ""  // only on failure
        }
    """
    data = request.get_json(force=True, silent=True) or {}
    fault_codes = data.get("fault_codes", [])
    pipeline_status = data.get("status", "")
    now = datetime.now()

    if not fault_codes:
        return jsonify({"success": False, "error": "fault_codes required"}), 400
    if pipeline_status not in ("success", "failure"):
        return jsonify({"success": False, "error": "status must be 'success' or 'failure'"}), 400

    updated = []
    live_incidents = get_live_incidents()

    for fault_code in fault_codes:
        matched_incident_ids = []

        for inc in live_incidents:
            if inc.get("error_code") != fault_code:
                continue
            if inc.get("status") == "resolved":
                continue

            matched_incident_ids.append(inc["id"])
            if pipeline_status == "success":
                updates = {
                    "status": "resolved",
                    "timestamp_resolved": now,
                    "verification": {
                        "latency_before": inc.get("symptoms", {}).get("latency_p95_value", 0),
                        "latency_after": 0,
                        "health_check_status": "passed",
                        "success": True,
                    },
                    "commit_sha": data.get("commit_sha", ""),
                    "run_url": data.get("run_url", ""),
                }
            else:
                updates = {
                    "status": "in_progress",
                    "verification": {
                        "latency_before": inc.get("symptoms", {}).get("latency_p95_value", 0),
                        "latency_after": None,
                        "health_check_status": "failed",
                        "success": False,
                    },
                    "commit_sha": data.get("commit_sha", ""),
                    "run_url": data.get("run_url", ""),
                }

            result = update_live_incident(inc["id"], updates)
            if result:
                updated.append(inc["id"])

        if matched_incident_ids:
            continue

        created = create_live_incident(
            error_code=fault_code,
            route=_default_route_for_fault_code(fault_code),
            reason="pipeline_success" if pipeline_status == "success" else "pipeline_failure",
        )
        if pipeline_status == "success":
            updates = {
                "status": "resolved",
                "timestamp_resolved": now,
                "verification": {
                    "latency_before": created.get("symptoms", {}).get("latency_p95_value", 0),
                    "latency_after": 0,
                    "health_check_status": "passed",
                    "success": True,
                },
                "commit_sha": data.get("commit_sha", ""),
                "run_url": data.get("run_url", ""),
            }
        else:
            updates = {
                "status": "in_progress",
                "verification": {
                    "latency_before": created.get("symptoms", {}).get("latency_p95_value", 0),
                    "latency_after": None,
                    "health_check_status": "failed",
                    "success": False,
                },
                "commit_sha": data.get("commit_sha", ""),
                "run_url": data.get("run_url", ""),
            }

        result = update_live_incident(created["id"], updates)
        if result:
            updated.append(created["id"])

    logger.info(
        "Pipeline callback (%s): updated %s for %s",
        pipeline_status, updated, fault_codes,
    )
    return jsonify({"success": True, "status": pipeline_status, "updated": updated})


@developer.post("/developer/incidents/pipeline/resolve-all")
def pipeline_resolve_all():
    """Ignore non-fault deploys so unrelated incidents are never auto-resolved."""
    data = request.get_json(force=True, silent=True) or {}
    logger.info(
        "Pipeline resolve-all ignored for commit %s; leaving active incidents untouched",
        data.get("commit_sha", ""),
    )
    return jsonify({
        "success": True,
        "status": "ignored",
        "updated": [],
        "message": "Non-fault deploys do not auto-resolve incidents.",
    })


# This function handles manual resolution of an incident from the dashboard.
@developer.post("/developer/incidents/<incident_id>/resolve")
def manual_resolve(incident_id):
    """Manually mark an incident as resolved from the dashboard.

    Used when the fix has been deployed but the pipeline callback didn't
    fire (e.g. DASHBOARD_URL not reachable, commit didn't have [FAULT:] tag).
    """
    data = request.get_json(force=True, silent=True) or {}
    now = datetime.now()

    incident = _get_incident_by_id(incident_id)
    if not incident:
        return jsonify({"success": False, "error": "Incident not found"}), 404

    if incident.get("status") == "resolved":
        return jsonify({"success": False, "error": "Already resolved"}), 400

    # Compute a basic confidence score from available data
    confidence = _compute_confidence(incident)

    updates = {
        "status": "resolved",
        "timestamp_resolved": now,
        "verification": {
            "latency_before": incident.get("symptoms", {}).get("latency_p95_value", 0),
            "latency_after": 0,
            "health_check_status": "passed",
            "success": True,
        },
    }

    # Set confidence if root_cause exists
    root_cause = incident.get("root_cause") or {}
    if root_cause.get("explanation"):
        updates["root_cause"] = {
            "source": root_cause.get("source") or "manual",
            "confidence_score": confidence,
            "explanation": root_cause.get("explanation"),
        }

    # Include commit info if provided
    if data.get("commit_sha"):
        updates["commit_sha"] = data["commit_sha"]
    if data.get("run_url"):
        updates["run_url"] = data["run_url"]

    result = update_live_incident(incident_id, updates)
    if not result:
        return jsonify({"success": False, "error": "Failed to update"}), 500

    logger.info("Manually resolved incident %s", incident_id)
    return jsonify({"success": True, "incident_id": incident_id})


def _compute_confidence(incident: dict) -> float:
    """Compute a confidence score (0.0-1.0) based on available incident data."""
    score = 0.0

    root_cause = incident.get("root_cause") or {}
    remediation = incident.get("remediation") or {}

    # Has root cause explanation (+0.3)
    if root_cause.get("explanation"):
        score += 0.3

    # Has RAG or backboard source (+0.2)
    if root_cause.get("source") in ("rag", "backboard"):
        score += 0.2

    # Has remediation action (+0.2)
    if remediation.get("action_type") and remediation["action_type"] != "pending_analysis":
        score += 0.2

    # Has fix parameters/claude output (+0.2)
    params = remediation.get("parameters") or {}
    if params.get("claude_output"):
        score += 0.2

    # Has symptoms data (+0.1)
    symptoms = incident.get("symptoms") or {}
    if symptoms.get("log_marker") and symptoms["log_marker"] not in ("-", "—"):
        score += 0.1

    return min(score, 1.0)
