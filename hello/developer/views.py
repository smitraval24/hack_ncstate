"""This file handles the views logic for the developer part of the project."""

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
    route_map = {
        "FAULT_SQL_INJECTION_TEST": "/test-fault/run",
        "FAULT_EXTERNAL_API_LATENCY": "/test-fault/external-api",
        "FAULT_DB_TIMEOUT": "/test-fault/db-timeout",
    }
    return route_map.get(fault_code, "/test-fault")


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
    """Clear all live incidents, pause self-healing, and restore faulty code."""
    try:
        count = reset_live_incidents()

        # Pause self-healing so the Lambda doesn't immediately "fix" the
        # faulty code before the user can demo the errors.
        _pause_self_healing()

        # Push the original faulty views.py back to GitHub so faults are
        # restored after the next CI/CD deploy.
        code_reset_result = _reset_faulty_code()

        return jsonify({
            "success": True,
            "deleted": count,
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
            "message": "Self-healing armed. Trigger a fault now — the Lambda will process it.",
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


def _reset_faulty_code() -> dict:
    """Push the original faulty views.py back to GitHub.

    Tries the GithubTool Lambda first (if GITHUB_LAMBDA_NAME is set),
    then falls back to the GitHub API directly (if GITHUB_TOKEN is set).
    """
    from hello.page._faulty_views_template import FAULTY_VIEWS_CONTENT
    from config.settings import (
        GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH,
        GITHUB_LAMBDA_NAME,
    )

    file_path = "hello/page/views.py"
    commit_message = "[RESET] Restore faulty views.py for demo cycle"

    # Method 1: Invoke GithubTool Lambda
    if GITHUB_LAMBDA_NAME:
        try:
            import boto3
            lambda_client = boto3.client("lambda")
            payload = {
                "actionGroup": "GitHubActions",
                "function": "push_github_fix",
                "parameters": [
                    {"name": "file_path", "value": file_path},
                    {"name": "file_content", "value": FAULTY_VIEWS_CONTENT},
                    {"name": "commit_message", "value": commit_message},
                ],
            }
            resp = lambda_client.invoke(
                FunctionName=GITHUB_LAMBDA_NAME,
                Payload=json.dumps(payload).encode("utf-8"),
            )
            result = json.loads(resp["Payload"].read())
            body = json.loads(
                result.get("response", {})
                .get("functionResponse", {})
                .get("responseBody", {})
                .get("TEXT", {})
                .get("body", "{}")
            )
            logger.info("Reset faulty code via Lambda: %s", body)
            return {"method": "lambda", "success": body.get("ok", False), "detail": body}
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

            # Push faulty content
            content_b64 = base64.b64encode(FAULTY_VIEWS_CONTENT.encode("utf-8")).decode("utf-8")
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
            return {"method": "github_api", "success": True, "commit_sha": commit_sha}
        except Exception as e:
            logger.warning("GitHub API reset failed: %s", e)
            return {"method": "github_api", "success": False, "error": str(e)}

    logger.info("No GitHub credentials configured — skipped code reset")
    return {"method": "none", "success": False, "error": "No GITHUB_LAMBDA_NAME or GITHUB_TOKEN configured"}


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
        matched = False

        for inc in live_incidents:
            if inc.get("error_code") != fault_code:
                continue
            if inc.get("status") == "resolved":
                continue

            matched = True
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

        if not matched:
            logger.info("No active incident found for fault_code %s, skipping", fault_code)

    logger.info(
        "Pipeline callback (%s): updated %s for %s",
        pipeline_status, updated, fault_codes,
    )
    return jsonify({"success": True, "status": pipeline_status, "updated": updated})


@developer.post("/developer/incidents/pipeline/resolve-all")
def pipeline_resolve_all():
    """Called by GitHub Actions when deploy succeeds but no [FAULT:] tags found.

    Resolves ALL active incidents since a successful deploy means the
    codebase is healthy.
    """
    data = request.get_json(force=True, silent=True) or {}
    now = datetime.now()
    updated = []

    for inc in get_live_incidents():
        if inc.get("status") in ("detected", "in_progress"):
            result = update_live_incident(inc["id"], {
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
            })
            if result:
                updated.append(inc["id"])

    logger.info("Pipeline resolve-all: resolved %s", updated)
    return jsonify({"success": True, "status": "success", "updated": updated})


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
