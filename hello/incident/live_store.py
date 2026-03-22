"""PostgreSQL-backed live incident store for the developer dashboard.

When a fault is injected, an incident is created here in the developer-
dashboard-compatible format (nested dicts) and persisted to PostgreSQL.
This survives container restarts and ECS deployments.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import inspect

from hello.extensions import db
from hello.incident.models import LiveIncident

logger = logging.getLogger(__name__)

_table_checked = False


def _ensure_table() -> None:
    """Create the live_incidents table if it doesn't exist yet."""
    global _table_checked
    if _table_checked:
        return
    try:
        if not inspect(db.engine).has_table("live_incidents"):
            LiveIncident.__table__.create(db.engine)
            logger.info("Created live_incidents table")
        _table_checked = True
    except Exception:
        logger.warning("Could not verify/create live_incidents table", exc_info=True)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize(inc: dict) -> str:
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


def _deserialize(raw: str) -> dict:
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
    "FAULT_EXTERNAL_API_LATENCY": ("External API Degradation", "critical"),
    "FAULT_DB_TIMEOUT": ("Database Statement Timeout", "critical"),
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


def _next_incident_id() -> str:
    """Generate the next LIVE-NNNN id based on the max existing id."""
    row = db.session.query(db.func.max(LiveIncident.id)).scalar() or 0
    return f"LIVE-{row + 1:04d}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_incident(
    error_code: str,
    route: str,
    reason: str,
    latency: float | None = None,
) -> dict:
    """Create a new live incident and persist it to PostgreSQL."""
    _ensure_table()
    incident_id = _next_incident_id()
    incident = _build_incident(incident_id, error_code, route, reason, latency)

    row = LiveIncident(incident_id=incident_id, data=_serialize(incident))
    db.session.add(row)
    db.session.commit()

    logger.info("Created live incident %s for %s", incident_id, error_code)
    return incident


def update_incident(incident_id: str, updates: dict) -> dict | None:
    """Update fields on a live incident in PostgreSQL."""
    _ensure_table()
    row = LiveIncident.query.filter_by(incident_id=incident_id).first()
    if not row:
        return None

    incident = _deserialize(row.data)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(incident.get(key), dict):
            incident[key].update(value)
        else:
            incident[key] = value

    row.data = _serialize(incident)
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return incident


def get_incident(incident_id: str) -> dict | None:
    """Get a single live incident by ID."""
    _ensure_table()
    row = LiveIncident.query.filter_by(incident_id=incident_id).first()
    if not row:
        return None
    return _deserialize(row.data)


def get_all_incidents() -> list[dict]:
    """Return all live incidents, most recent first."""
    _ensure_table()
    rows = LiveIncident.query.order_by(LiveIncident.created_at.desc()).all()
    return [_deserialize(row.data) for row in rows]


def reset_all() -> int:
    """Delete all live incidents from PostgreSQL. Returns count deleted."""
    _ensure_table()
    count = LiveIncident.query.delete()
    db.session.commit()
    return count
