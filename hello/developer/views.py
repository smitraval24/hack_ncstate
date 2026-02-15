import os
from datetime import datetime, timedelta

from flask import Blueprint, render_template

from config.settings import CLOUDWATCH_ENABLED
from hello.aws.cloudwatch_logs import (
    build_fault_router_incidents,
    build_incidents_from_events,
    fetch_recent_events,
    get_cloudwatch_log_groups,
)

developer = Blueprint("developer", __name__, template_folder="templates")


# Mock incident data - in future, this will come from database
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
    resolved_incidents = [i for i in incidents if i.get("status") == "resolved"]
    auto_resolved_count = len(
        [i for i in resolved_incidents if (i.get("verification") or {}).get("success")]
    )
    auto_resolution_rate = (auto_resolved_count / len(resolved_incidents) * 100) if resolved_incidents else 0

    # Calculate MTTR (Mean Time To Remediate)
    resolution_times = []
    for incident in resolved_incidents:
        if incident.get("timestamp_resolved"):
            delta = incident["timestamp_resolved"] - incident["timestamp_opened"]
            resolution_times.append(delta.total_seconds() / 60)  # minutes

    mttr = sum(resolution_times) / len(resolution_times) if resolution_times else 0

    return {
        "active_incidents": active_count,
        "resolved_total": resolved_count,
        "resolved_today": resolved_today_count,
        "auto_resolution_rate": round(auto_resolution_rate, 1),
        "mttr": round(mttr, 1),
        "total_incidents": len(incidents)
    }


@developer.get("/developer/incidents")
def incidents_dashboard():
    """Main incidents dashboard page"""
    data_source = "mock"
    cloudwatch_error = None

    incidents: list[dict]
    if CLOUDWATCH_ENABLED:
        cw_incidents, cloudwatch_error = get_cloudwatch_incidents()
        if cw_incidents:
            incidents = cw_incidents
            data_source = "cloudwatch"
        else:
            incidents = get_mock_incidents()
            data_source = "mock"
    else:
        incidents = get_mock_incidents()

    metrics = get_dashboard_metrics(incidents)

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
        data_source=data_source,
        cloudwatch_error=cloudwatch_error,
        type_counts=type_counts,
    )


@developer.get("/developer/incidents/<incident_id>")
def incident_detail(incident_id):
    """Incident detail page"""
    incidents: list[dict]
    if CLOUDWATCH_ENABLED:
        cw_incidents, _err = get_cloudwatch_incidents()
        incidents = cw_incidents or get_mock_incidents()
    else:
        incidents = get_mock_incidents()
    incident = next((i for i in incidents if i["id"] == incident_id), None)

    if not incident:
        return "Incident not found", 404

    return render_template(
        "developer/incident_detail.html",
        incident=incident
    )
