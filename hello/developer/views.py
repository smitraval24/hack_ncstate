"""This file handles the views logic for the developer part of the project."""

import json
import logging
import os
from datetime import datetime, timedelta

import redis
from flask import Blueprint, current_app, jsonify, render_template, request

from config.settings import CLOUDWATCH_ENABLED
from hello.aws.cloudwatch_logs import (
    build_fault_router_incidents,
    build_incidents_from_events,
    fetch_recent_events,
    get_cloudwatch_log_groups,
)
from hello.incident.live_store import (
    get_all_incidents as get_live_incidents,
    get_incident as get_live_incident,
    reset_all as reset_live_incidents,
    update_incident as update_live_incident,
)

logger = logging.getLogger(__name__)

# This blueprint groups related routes for this part of the app.
developer = Blueprint("developer", __name__, template_folder="templates")


# Mock incident data - in future, this will come from database
# This function gets the mock incidents work used in this file.
def get_mock_incidents():
    """Generate realistic mock incident data"""
    now = datetime.now()

    incidents = [
        {
            "id": "INC-001",
            "timestamp_opened": now - timedelta(hours=2),
            "timestamp_resolved": now - timedelta(hours=1, minutes=45),
            "incident_type": "External API Timeout",
            "severity": "high",
            "status": "resolved",
            "route": "/test-fault/external-api",
            "error_code": "FAULT_EXTERNAL_API_LATENCY",
            "symptoms": {
                "error_rate": "28%",
                "error_rate_value": 28,
                "latency_p95": "4.2s",
                "latency_p95_value": 4.2,
                "endpoint": "/test-fault/external-api",
                "log_marker": "external_timeout",
                "affected_requests": 142
            },
            "breadcrumbs": {
                "recent_logs": [
                    "2026-02-14 14:03:15 ERROR FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=external_timeout latency=3.21",
                    "2026-02-14 14:03:18 ERROR FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=external_timeout latency=3.05",
                    "2026-02-14 14:03:21 ERROR FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=upstream_failure latency=2.87",
                    "2026-02-14 14:03:24 INFO external_call_latency=5.42",
                    "2026-02-14 14:03:27 ERROR FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=external_timeout latency=3.18"
                ],
                "metric_snapshot": {
                    "total_requests": 500,
                    "failed_requests": 142,
                    "avg_latency": "3.2s",
                    "timestamp": "2026-02-14 14:03:15"
                },
                "correlated_events": [
                    "External API mock_api:5001 responding slowly",
                    "Request timeout threshold (3s) exceeded",
                    "Connection pool showing signs of exhaustion"
                ]
            },
            "root_cause": {
                "source": "simulated",
                "confidence_score": 0.89,
                "explanation": "External API latency spike causing timeouts. Mock API service injecting 2-8s delays with 60% probability. Requests timing out after 3s threshold."
            },
            "remediation": {
                "action_type": "enable_fallback_mode",
                "parameters": {"external_api": "mock_api:5001", "fallback": "cached_response"},
                "execution_timestamp": now - timedelta(hours=1, minutes=50)
            },
            "verification": {
                "error_rate_before": 28,
                "error_rate_after": 3,
                "latency_before": 4.2,
                "latency_after": 0.8,
                "health_check_status": "passed",
                "success": True
            }
        },
        {
            "id": "INC-002",
            "timestamp_opened": now - timedelta(minutes=30),
            "timestamp_resolved": None,
            "incident_type": "Database Connection Pool Exhaustion",
            "severity": "critical",
            "status": "in_progress",
            "route": "/test-fault/db-timeout",
            "error_code": "FAULT_DB_TIMEOUT",
            "symptoms": {
                "error_rate": "45%",
                "error_rate_value": 45,
                "latency_p95": "7.1s",
                "latency_p95_value": 7.1,
                "endpoint": "/test-fault/db-timeout",
                "log_marker": "db_timeout_or_pool_exhaustion",
                "affected_requests": 89
            },
            "breadcrumbs": {
                "recent_logs": [
                    "2026-02-14 15:30:42 ERROR FAULT_DB_TIMEOUT route=/test-fault/db-timeout reason=db_timeout_or_pool_exhaustion latency=5.12",
                    "2026-02-14 15:30:48 ERROR FAULT_DB_TIMEOUT route=/test-fault/db-timeout reason=db_timeout_or_pool_exhaustion latency=5.34",
                    "2026-02-14 15:30:51 ERROR db_error=QueuePool limit of size 5 overflow 10 reached",
                    "2026-02-14 15:30:54 ERROR FAULT_DB_TIMEOUT route=/test-fault/db-timeout reason=db_timeout_or_pool_exhaustion latency=5.01"
                ],
                "metric_snapshot": {
                    "total_requests": 200,
                    "failed_requests": 89,
                    "avg_latency": "5.2s",
                    "timestamp": "2026-02-14 15:30:42"
                },
                "correlated_events": [
                    "Database connection pool limit reached (5 connections)",
                    "Multiple pg_sleep(5) queries blocking connections",
                    "Queue pool overflow exhausted"
                ]
            },
            "root_cause": {
                "source": "simulated",
                "confidence_score": 0.92,
                "explanation": "Database connection pool exhaustion due to long-running queries (pg_sleep). With pool size of 5 and queries holding connections for 5+ seconds, new requests cannot acquire connections."
            },
            "remediation": {
                "action_type": "increase_pool_size",
                "parameters": {"current_size": 5, "new_size": 20, "overflow": 20},
                "execution_timestamp": now - timedelta(minutes=25)
            },
            "verification": {
                "error_rate_before": 45,
                "error_rate_after": 12,
                "latency_before": 7.1,
                "latency_after": 5.3,
                "health_check_status": "degraded",
                "success": False
            }
        },
        {
            "id": "INC-003",
            "timestamp_opened": now - timedelta(days=1, hours=3),
            "timestamp_resolved": now - timedelta(days=1, hours=2),
            "incident_type": "SQL Syntax Error",
            "severity": "medium",
            "status": "resolved",
            "route": "/test-fault/run",
            "error_code": "FAULT_SQL_INJECTION_TEST",
            "symptoms": {
                "error_rate": "100%",
                "error_rate_value": 100,
                "latency_p95": "0.05s",
                "latency_p95_value": 0.05,
                "endpoint": "/test-fault/run",
                "log_marker": "invalid_sql_executed",
                "affected_requests": 8
            },
            "breadcrumbs": {
                "recent_logs": [
                    "2026-02-13 13:02:11 ERROR FAULT_SQL_INJECTION_TEST route=/test-fault/run reason=invalid_sql_executed",
                    "2026-02-13 13:02:15 ERROR syntax error at or near 'FROM'",
                    "2026-02-13 13:02:18 ERROR FAULT_SQL_INJECTION_TEST route=/test-fault/run reason=invalid_sql_executed"
                ],
                "metric_snapshot": {
                    "total_requests": 8,
                    "failed_requests": 8,
                    "avg_latency": "0.04s",
                    "timestamp": "2026-02-13 13:02:11"
                },
                "correlated_events": [
                    "SQL query missing SELECT clause: 'SELECT FROM'",
                    "PostgreSQL syntax error triggered",
                    "Fault injection test route invoked"
                ]
            },
            "root_cause": {
                "source": "simulated",
                "confidence_score": 0.98,
                "explanation": "Intentional SQL syntax error from fault injection test. Query 'SELECT FROM' is missing column specification, triggering PostgreSQL syntax error."
            },
            "remediation": {
                "action_type": "query_validation",
                "parameters": {"add_sql_linting": True, "enable_parameterization": True},
                "execution_timestamp": now - timedelta(days=1, hours=2, minutes=30)
            },
            "verification": {
                "error_rate_before": 100,
                "error_rate_after": 0,
                "latency_before": 0.05,
                "latency_after": 0.02,
                "health_check_status": "passed",
                "success": True
            }
        },
        {
            "id": "INC-004",
            "timestamp_opened": now - timedelta(hours=5),
            "timestamp_resolved": now - timedelta(hours=4, minutes=30),
            "incident_type": "External API HTTP 500",
            "severity": "high",
            "status": "resolved",
            "route": "/test-fault/external-api",
            "error_code": "FAULT_EXTERNAL_API_LATENCY",
            "symptoms": {
                "error_rate": "32%",
                "error_rate_value": 32,
                "latency_p95": "2.3s",
                "latency_p95_value": 2.3,
                "endpoint": "/test-fault/external-api",
                "log_marker": "upstream_failure",
                "affected_requests": 67
            },
            "breadcrumbs": {
                "recent_logs": [
                    "2026-02-14 11:05:33 ERROR FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=upstream_failure latency=2.11",
                    "2026-02-14 11:05:36 ERROR HTTP 500 from mock_api:5001/data",
                    "2026-02-14 11:05:39 ERROR FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=upstream_failure latency=2.28"
                ],
                "metric_snapshot": {
                    "total_requests": 210,
                    "failed_requests": 67,
                    "avg_latency": "2.2s",
                    "timestamp": "2026-02-14 11:05:33"
                },
                "correlated_events": [
                    "External API returning HTTP 500 errors",
                    "Mock API fault mode: 30% error injection",
                    "Upstream service degradation detected"
                ]
            },
            "root_cause": {
                "source": "simulated",
                "confidence_score": 0.87,
                "explanation": "External API mock service injecting HTTP 500 errors with 30% probability. Upstream service experiencing simulated degradation."
            },
            "remediation": {
                "action_type": "enable_circuit_breaker",
                "parameters": {"service": "mock_api:5001", "threshold": 0.3, "timeout": "30s"},
                "execution_timestamp": now - timedelta(hours=4, minutes=45)
            },
            "verification": {
                "error_rate_before": 32,
                "error_rate_after": 5,
                "latency_before": 2.3,
                "latency_after": 1.1,
                "health_check_status": "passed",
                "success": True
            }
        },
        {
            "id": "INC-005",
            "timestamp_opened": now - timedelta(minutes=10),
            "timestamp_resolved": None,
            "incident_type": "Connection Refused",
            "severity": "critical",
            "status": "detected",
            "route": "/test-fault/external-api",
            "error_code": "FAULT_EXTERNAL_API_LATENCY",
            "symptoms": {
                "error_rate": "100%",
                "error_rate_value": 100,
                "latency_p95": "0.2s",
                "latency_p95_value": 0.2,
                "endpoint": "/test-fault/external-api",
                "log_marker": "connection_error",
                "affected_requests": 15
            },
            "breadcrumbs": {
                "recent_logs": [
                    "2026-02-14 15:50:12 ERROR FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=connection_error latency=0.18",
                    "2026-02-14 15:50:15 ERROR Connection refused to mock_api:5001",
                    "2026-02-14 15:50:18 ERROR FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=connection_error latency=0.21"
                ],
                "metric_snapshot": {
                    "total_requests": 15,
                    "failed_requests": 15,
                    "avg_latency": "0.19s",
                    "timestamp": "2026-02-14 15:50:12"
                },
                "correlated_events": [
                    "External API service unreachable",
                    "Connection attempts failing immediately",
                    "Possible service down or network partition"
                ]
            },
            "root_cause": {
                "source": "simulated",
                "confidence_score": 0.95,
                "explanation": "External API service (mock_api:5001) is unreachable. All connection attempts failing with 'Connection refused'. Service may be down or network issue."
            },
            "remediation": {
                "action_type": "pending_analysis",
                "parameters": {},
                "execution_timestamp": None
            },
            "verification": {
                "error_rate_before": 100,
                "error_rate_after": None,
                "latency_before": 0.2,
                "latency_after": None,
                "health_check_status": "unknown",
                "success": None
            }
        }
    ]

    return incidents


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
        incidents = get_mock_incidents()
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


# This function handles the sync status work for this file.
def _sync_status(incidents: list[dict]) -> list[dict]:
    """Derive status from verification result so it stays in sync.

    Applies to ALL data sources (live, CloudWatch, mock) so that an incident
    whose verification.success is True always shows as 'resolved'.
    """
    now = datetime.now()
    for inc in incidents:
        verification = inc.get("verification") or {}
        if verification.get("success") is True:
            inc["status"] = "resolved"
            if not inc.get("timestamp_resolved"):
                inc["timestamp_resolved"] = now
        elif verification.get("success") is False:
            inc["status"] = "in_progress"
        # else keep as-is (detected/pending)
    return incidents


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
# This function handles the incidents dashboard work for this file.
def incidents_dashboard():
    """Main incidents dashboard page"""
    cloudwatch_lookback_minutes: int | None = None
    if CLOUDWATCH_ENABLED:
        try:
            cloudwatch_lookback_minutes = int(os.getenv("CLOUDWATCH_LOOKBACK_MINUTES", "120"))
        except Exception:
            cloudwatch_lookback_minutes = None

    incidents, data_source, cloudwatch_error = _fetch_incidents()

    metrics = get_dashboard_metrics(incidents)
    trend_data = build_incident_trend(incidents)

    # Drive the "Incident Types" chart from real data.
    type_counts = {
        "external_api": 0,
        "db": 0,
        "sql": 0,
        "connection": 0,
    }
    for inc in incidents:
        t = (inc.get("incident_type") or "").lower()
        ec = (inc.get("error_code") or "").lower()
        if "external api" in t or "external_api" in ec:
            type_counts["external_api"] += 1
        elif "database" in t or "db" in ec:
            type_counts["db"] += 1
        elif "sql" in t or "sql" in ec:
            type_counts["sql"] += 1
        else:
            type_counts["connection"] += 1

    return render_template(
        "developer/incidents.html",
        incidents=incidents,
        metrics=metrics,
        trend_data=trend_data,
        data_source=data_source,
        cloudwatch_error=cloudwatch_error,
        cloudwatch_lookback_minutes=cloudwatch_lookback_minutes,
        type_counts=type_counts,
    )


# This function handles the incidents api data work for this file.
@developer.get("/developer/incidents/api/data")
# This function handles the incidents api data work for this file.
def incidents_api_data():
    """JSON API for real-time dashboard updates via polling."""
    incidents, data_source, _ = _fetch_incidents()

    metrics = get_dashboard_metrics(incidents)
    trend_data = build_incident_trend(incidents)

    type_counts = {"external_api": 0, "db": 0, "sql": 0, "connection": 0}
    for inc in incidents:
        t = (inc.get("incident_type") or "").lower()
        ec = (inc.get("error_code") or "").lower()
        if "external api" in t or "external_api" in ec:
            type_counts["external_api"] += 1
        elif "database" in t or "db" in ec:
            type_counts["db"] += 1
        elif "sql" in t or "sql" in ec:
            type_counts["sql"] += 1
        else:
            type_counts["connection"] += 1

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
        serialized.append(s)

    return jsonify({
        "metrics": metrics,
        "trend_data": trend_data,
        "type_counts": type_counts,
        "incidents": serialized,
        "data_source": data_source,
    })


# This function handles the incident detail work for this file.
@developer.get("/developer/incidents/<incident_id>")
# This function handles the incident detail work for this file.
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
        f"Error Rate: {incident.get('symptoms', {}).get('error_rate', 'N/A')}",
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
# This function handles the store in rag work for this file.
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
# This function handles the store in cache work for this file.
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
# This function handles the reset incidents work for this file.
def reset_incidents():
    """Clear all live incidents from the store."""
    try:
        count = reset_live_incidents()
        return jsonify({"success": True, "deleted": count})
    except Exception as e:
        logger.exception("Failed to reset incidents")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Pipeline callback endpoints (called by Lambda + GitHub Actions)
# ---------------------------------------------------------------------------

# This function handles the pipeline pending work for this file.
@developer.post("/developer/incidents/pipeline/pending")
# This function handles the pipeline pending work for this file.
def pipeline_pending():
    """Called by the Lambda after Claude pushes a fix but before deploy.

    Updates matching live incidents to 'in_progress' and stores the RAG
    analysis and Claude output so the dashboard shows remediation is underway.
    """
    data = request.get_json(force=True, silent=True) or {}
    fault_code = data.get("fault_code", "")
    if not fault_code:
        return jsonify({"success": False, "error": "fault_code required"}), 400

    updated = []
    for inc in get_live_incidents():
        if inc.get("error_code") == fault_code and inc.get("status") == "detected":
            result = update_live_incident(inc["id"], {
                "status": "in_progress",
                "root_cause": {
                    "source": "rag",
                    "confidence_score": None,
                    "explanation": data.get("rag_analysis", ""),
                },
                "remediation": {
                    "action_type": "auto_fix_pushed",
                    "parameters": {"claude_output": data.get("claude_output", "")},
                    "execution_timestamp": datetime.now(),
                },
            })
            if result:
                updated.append(inc["id"])

    logger.info("Pipeline pending: updated %s for %s", updated, fault_code)
    return jsonify({"success": True, "updated": updated})


# This function handles the pipeline callback work for this file.
@developer.post("/developer/incidents/pipeline/callback")
# This function handles the pipeline callback work for this file.
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
    for inc in get_live_incidents():
        if inc.get("error_code") not in fault_codes:
            continue
        if inc.get("status") == "resolved":
            continue

        if pipeline_status == "success":
            updates = {
                "status": "resolved",
                "timestamp_resolved": now,
                "verification": {
                    "error_rate_before": inc.get("symptoms", {}).get("error_rate_value", 0),
                    "error_rate_after": 0,
                    "latency_before": inc.get("symptoms", {}).get("latency_p95_value", 0),
                    "latency_after": 0,
                    "health_check_status": "passed",
                    "success": True,
                },
            }
        else:
            updates = {
                "status": "in_progress",
                "verification": {
                    "error_rate_before": inc.get("symptoms", {}).get("error_rate_value", 0),
                    "error_rate_after": None,
                    "latency_before": inc.get("symptoms", {}).get("latency_p95_value", 0),
                    "latency_after": None,
                    "health_check_status": "failed",
                    "success": False,
                },
            }

        result = update_live_incident(inc["id"], updates)
        if result:
            updated.append(inc["id"])

    logger.info(
        "Pipeline callback (%s): updated %s for %s",
        pipeline_status, updated, fault_codes,
    )
    return jsonify({"success": True, "status": pipeline_status, "updated": updated})
