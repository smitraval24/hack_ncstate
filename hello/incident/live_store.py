"""Redis-backed live incident store for the developer dashboard.

When a fault is injected, an incident is created here in the developer-
dashboard-compatible format (nested dicts).  The developer dashboard reads
from this store instead of mock data, giving real-time visibility into
injected faults.

Falls back to an in-memory store when Redis is unavailable so incidents
are never silently lost.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any

import redis
from flask import current_app

logger = logging.getLogger(__name__)

INCIDENT_LIST_KEY = "live_incidents:list"
INCIDENT_KEY_PREFIX = "live_incidents:detail:"
INCIDENT_COUNTER_KEY = "live_incidents:counter"

# ---------------------------------------------------------------------------
# In-memory fallback (used when Redis is unreachable)
# ---------------------------------------------------------------------------
_mem_lock = threading.Lock()
_mem_incidents: dict[str, dict] = {}  # id -> incident dict
_mem_order: list[str] = []            # newest first
_mem_counter: int = 0


def _redis() -> redis.Redis:
    url = current_app.config.get("REDIS_URL", "redis://redis:6379/0")
    return redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)


def _redis_available() -> redis.Redis | None:
    """Return a Redis client if reachable, else None."""
    try:
        r = _redis()
        r.ping()
        return r
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

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


def _deserialize_incident(raw: str) -> dict:
    """JSON-decode an incident, converting ISO strings back to datetimes."""
    inc = json.loads(raw)
    for key in ("timestamp_opened", "timestamp_resolved"):
        if inc.get(key):
            try:
                inc[key] = datetime.fromisoformat(inc[key])
            except (ValueError, TypeError):
                pass
    rem = inc.get("remediation") or {}
    if rem.get("execution_timestamp"):
        try:
            rem["execution_timestamp"] = datetime.fromisoformat(rem["execution_timestamp"])
        except (ValueError, TypeError):
            pass
    return inc


# ---------------------------------------------------------------------------
# Incident type / severity mapping
# ---------------------------------------------------------------------------
_TYPE_MAP = {
    "FAULT_SQL_INJECTION_TEST": ("SQL Injection Error", "high"),
    "FAULT_EXTERNAL_API_LATENCY": ("External API Timeout", "critical"),
    "FAULT_DB_TIMEOUT": ("Database Connection Pool Exhaustion", "critical"),
}


def _build_incident(
    incident_id: str,
    error_code: str,
    route: str,
    reason: str,
    latency: float | None = None,
) -> dict:
    """Build an incident dict in dashboard-compatible format."""
    now = datetime.now()
    incident_type, severity = _TYPE_MAP.get(error_code, ("Application Error", "medium"))

    return {
        "id": incident_id,
        "timestamp_opened": now,
        "timestamp_resolved": None,
        "incident_type": incident_type,
        "severity": severity,
        "status": "detected",
        "route": route,
        "error_code": error_code,
        "symptoms": {
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
            "latency_before": None,
            "latency_after": None,
            "health_check_status": None,
            "success": None,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_incident(
    error_code: str,
    route: str,
    reason: str,
    latency: float | None = None,
) -> dict:
    """Create a new live incident. Uses Redis when available, memory otherwise."""
    r = _redis_available()

    if r is not None:
        try:
            seq = r.incr(INCIDENT_COUNTER_KEY)
            incident_id = f"LIVE-{seq:04d}"
            incident = _build_incident(incident_id, error_code, route, reason, latency)
            r.set(f"{INCIDENT_KEY_PREFIX}{incident_id}", _serialize_incident(incident))
            r.lpush(INCIDENT_LIST_KEY, incident_id)
            logger.info("Created live incident %s for %s (redis)", incident_id, error_code)
            return incident
        except Exception:
            logger.warning("Redis write failed, falling back to memory", exc_info=True)

    # In-memory fallback
    global _mem_counter
    with _mem_lock:
        _mem_counter += 1
        incident_id = f"LIVE-{_mem_counter:04d}"
        incident = _build_incident(incident_id, error_code, route, reason, latency)
        _mem_incidents[incident_id] = incident
        _mem_order.insert(0, incident_id)
    logger.info("Created live incident %s for %s (memory)", incident_id, error_code)
    return incident


def update_incident(incident_id: str, updates: dict) -> dict | None:
    """Update fields on a live incident."""
    r = _redis_available()

    if r is not None:
        try:
            raw = r.get(f"{INCIDENT_KEY_PREFIX}{incident_id}")
            if raw:
                incident = _deserialize_incident(raw)
                for key, value in updates.items():
                    if isinstance(value, dict) and isinstance(incident.get(key), dict):
                        incident[key].update(value)
                    else:
                        incident[key] = value
                r.set(f"{INCIDENT_KEY_PREFIX}{incident_id}", _serialize_incident(incident))
                return incident
        except Exception:
            logger.warning("Redis update failed for %s, trying memory", incident_id, exc_info=True)

    # In-memory fallback
    with _mem_lock:
        incident = _mem_incidents.get(incident_id)
        if not incident:
            return None
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(incident.get(key), dict):
                incident[key].update(value)
            else:
                incident[key] = value
        _mem_incidents[incident_id] = incident
    return incident


def get_incident(incident_id: str) -> dict | None:
    """Get a single live incident by ID."""
    r = _redis_available()

    if r is not None:
        try:
            raw = r.get(f"{INCIDENT_KEY_PREFIX}{incident_id}")
            if raw:
                return _deserialize_incident(raw)
        except Exception:
            logger.warning("Redis read failed for %s, trying memory", incident_id, exc_info=True)

    with _mem_lock:
        return _mem_incidents.get(incident_id)


def get_all_incidents() -> list[dict]:
    """Return all live incidents, most recent first."""
    incidents: list[dict] = []
    r = _redis_available()

    if r is not None:
        try:
            ids = r.lrange(INCIDENT_LIST_KEY, 0, -1)
            for inc_id in ids:
                if isinstance(inc_id, bytes):
                    inc_id = inc_id.decode()
                raw = r.get(f"{INCIDENT_KEY_PREFIX}{inc_id}")
                if raw:
                    incidents.append(_deserialize_incident(raw))
        except Exception:
            logger.warning("Redis read-all failed, falling back to memory", exc_info=True)
            incidents = []

    # Always merge in-memory incidents (they may exist alongside Redis ones)
    with _mem_lock:
        redis_ids = {i["id"] for i in incidents}
        for inc_id in _mem_order:
            if inc_id not in redis_ids:
                inc = _mem_incidents.get(inc_id)
                if inc:
                    incidents.append(inc)

    return incidents


def reset_all() -> int:
    """Delete all live incidents. Returns count deleted."""
    count = 0
    r = _redis_available()

    if r is not None:
        try:
            ids = r.lrange(INCIDENT_LIST_KEY, 0, -1)
            for inc_id in ids:
                if isinstance(inc_id, bytes):
                    inc_id = inc_id.decode()
                r.delete(f"{INCIDENT_KEY_PREFIX}{inc_id}")
                count += 1
            r.delete(INCIDENT_LIST_KEY)
            r.delete(INCIDENT_COUNTER_KEY)
        except Exception:
            logger.warning("Redis reset failed", exc_info=True)

    # Also clear in-memory store
    global _mem_counter
    with _mem_lock:
        count += len(_mem_incidents)
        _mem_incidents.clear()
        _mem_order.clear()
        _mem_counter = 0

    return count
