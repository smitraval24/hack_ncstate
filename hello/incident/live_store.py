"""Redis-backed live incident store for the developer dashboard.

When a fault is injected, an incident is created here in the developer-
dashboard-compatible format (nested dicts).  The developer dashboard reads
from this store instead of mock data, giving real-time visibility into
injected faults.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

import redis
from flask import current_app

logger = logging.getLogger(__name__)

INCIDENT_LIST_KEY = "live_incidents:list"
INCIDENT_KEY_PREFIX = "live_incidents:detail:"
INCIDENT_COUNTER_KEY = "live_incidents:counter"


# This function handles the redis work for this file.
def _redis() -> redis.Redis:
    url = current_app.config.get("REDIS_URL", "redis://redis:6379/0")
    return redis.from_url(url)


# This function handles the serialize incident work for this file.
def _serialize_incident(inc: dict) -> str:
    """JSON-encode an incident, converting datetimes to ISO strings."""
    def _convert(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj

    safe = {}
    for k, v in inc.items():
        if isinstance(v, datetime):
            safe[k] = v.isoformat()
        elif isinstance(v, dict):
            safe[k] = {nk: _convert(nv) for nk, nv in v.items()}
        else:
            safe[k] = v
    return json.dumps(safe)


# This function handles the deserialize incident work for this file.
def _deserialize_incident(raw: str) -> dict:
    """JSON-decode an incident, converting ISO strings back to datetimes."""
    inc = json.loads(raw)
    for key in ("timestamp_opened", "timestamp_resolved"):
        if inc.get(key):
            try:
                inc[key] = datetime.fromisoformat(inc[key])
            except (ValueError, TypeError):
                pass
    # Convert nested execution_timestamp
    rem = inc.get("remediation") or {}
    if rem.get("execution_timestamp"):
        try:
            rem["execution_timestamp"] = datetime.fromisoformat(rem["execution_timestamp"])
        except (ValueError, TypeError):
            pass
    return inc


# This function creates the incident work used in this file.
def create_incident(
    error_code: str,
    route: str,
    reason: str,
    latency: float | None = None,
) -> dict:
    """Create a new live incident and store it in Redis.

    Returns the incident dict in developer-dashboard format.
    """
    r = _redis()
    seq = r.incr(INCIDENT_COUNTER_KEY)
    now = datetime.now()

    # Map error codes to incident types and severity
    type_map = {
        "FAULT_SQL_INJECTION_TEST": ("SQL Injection Error", "high"),
        "FAULT_EXTERNAL_API_LATENCY": ("External API Timeout", "critical"),
        "FAULT_DB_TIMEOUT": ("Database Connection Pool Exhaustion", "critical"),
    }
    incident_type, severity = type_map.get(error_code, ("Application Error", "medium"))

    incident_id = f"LIVE-{seq:04d}"

    incident = {
        "id": incident_id,
        "timestamp_opened": now,
        "timestamp_resolved": None,
        "incident_type": incident_type,
        "severity": severity,
        "status": "detected",
        "route": route,
        "error_code": error_code,
        "symptoms": {
            "error_rate": "—",
            "error_rate_value": 0,
            "latency_p95": f"{latency:.2f}s" if latency else "—",
            "latency_p95_value": latency or 0,
            "endpoint": route,
            "log_marker": reason,
            "affected_requests": 1,
        },
        "breadcrumbs": {
            "recent_logs": [
                f"{now.strftime('%Y-%m-%d %H:%M:%S')} ERROR {error_code} route={route} reason={reason}"
                + (f" latency={latency:.2f}" if latency else "")
            ],
            "metric_snapshot": {
                "total_requests": None,
                "failed_requests": 1,
                "avg_latency": f"{latency:.2f}s" if latency else None,
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "correlated_events": [
                f"Fault injection triggered on {route}",
            ],
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
            "error_rate_before": None,
            "error_rate_after": None,
            "latency_before": None,
            "latency_after": None,
            "health_check_status": None,
            "success": None,
        },
    }

    # Store in Redis
    r.set(f"{INCIDENT_KEY_PREFIX}{incident_id}", _serialize_incident(incident))
    r.lpush(INCIDENT_LIST_KEY, incident_id)

    logger.info("Created live incident %s for %s", incident_id, error_code)
    return incident


# This function updates the incident work used in this file.
def update_incident(incident_id: str, updates: dict) -> dict | None:
    """Update fields on a live incident in Redis."""
    r = _redis()
    raw = r.get(f"{INCIDENT_KEY_PREFIX}{incident_id}")
    if not raw:
        return None

    incident = _deserialize_incident(raw)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(incident.get(key), dict):
            incident[key].update(value)
        else:
            incident[key] = value

    r.set(f"{INCIDENT_KEY_PREFIX}{incident_id}", _serialize_incident(incident))
    return incident


# This function gets the incident work used in this file.
def get_incident(incident_id: str) -> dict | None:
    """Get a single live incident by ID."""
    r = _redis()
    raw = r.get(f"{INCIDENT_KEY_PREFIX}{incident_id}")
    if not raw:
        return None
    return _deserialize_incident(raw)


# This function gets the all incidents work used in this file.
def get_all_incidents() -> list[dict]:
    """Return all live incidents, most recent first."""
    r = _redis()
    ids = r.lrange(INCIDENT_LIST_KEY, 0, -1)
    incidents = []
    for inc_id in ids:
        if isinstance(inc_id, bytes):
            inc_id = inc_id.decode()
        raw = r.get(f"{INCIDENT_KEY_PREFIX}{inc_id}")
        if raw:
            incidents.append(_deserialize_incident(raw))
    return incidents


# This function handles the reset all work for this file.
def reset_all() -> int:
    """Delete all live incidents from Redis. Returns count deleted."""
    r = _redis()
    ids = r.lrange(INCIDENT_LIST_KEY, 0, -1)
    count = 0
    for inc_id in ids:
        if isinstance(inc_id, bytes):
            inc_id = inc_id.decode()
        r.delete(f"{INCIDENT_KEY_PREFIX}{inc_id}")
        count += 1
    r.delete(INCIDENT_LIST_KEY)
    r.delete(INCIDENT_COUNTER_KEY)
    return count
