"""Incident blueprint – API endpoints for the self-healing RAG pipeline.

These endpoints are consumed by an **external AWS log-monitoring service**
that detects errors, records incidents, triggers RAG analysis, and resolves
them.  The Flask app owns data storage, the Backboard RAG integration, and
the human-facing dashboard.

Endpoints (JSON API – called by AWS service)
---------------------------------------------
POST /incidents/                – record a new incident
POST /incidents/<id>/analyze    – run RAG analysis on an incident
POST /incidents/<id>/resolve    – mark resolved & index into Backboard
GET  /incidents/                – list all incidents
GET  /incidents/<id>            – single incident detail

Admin / setup
--------------
POST /incidents/setup-assistant – bootstrap the Backboard assistant

Dashboard (HTML – for humans)
------------------------------
GET  /incidents/dashboard       – incident dashboard
GET  /incidents/stream          – SSE stream for live updates
"""

from __future__ import annotations

import json
import queue
import threading

import redis
from flask import Blueprint, Response, current_app, jsonify, render_template, request

from hello.extensions import db
from hello.incident.analyzer import (
    analyze_incident,
    record_incident,
    resolve_incident,
)
from hello.incident.agent_workflow import approve_plan, build_agent_plan
from hello.incident.models import Incident
from hello.incident.rag_service import setup_assistant
from hello.incident.seed_knowledge_base import seed_knowledge_base

# ---------------------------------------------------------------------------
# Redis pub/sub channel for cross-worker SSE broadcasting
# ---------------------------------------------------------------------------
SSE_CHANNEL = "incidents:sse"

# Per-thread subscriber queues (filled by a background Redis listener)
_sse_listeners: list[queue.Queue] = []
_sse_lock = threading.Lock()
_subscriber_started = False


def _get_redis() -> redis.Redis:
    """Create a Redis client from the app config."""
    url = current_app.config.get("REDIS_URL", "redis://redis:6379/0")
    return redis.from_url(url)


def _publish_event(data: dict) -> None:
    """Publish an SSE event dict to Redis so all workers receive it."""
    try:
        r = _get_redis()
        r.publish(SSE_CHANNEL, json.dumps(data))
    except Exception:
        pass  # best-effort; don't break the API response


def _start_subscriber_thread() -> None:
    """Start a daemon thread that listens on Redis and fans-out to local queues."""
    global _subscriber_started
    if _subscriber_started:
        return
    _subscriber_started = True

    redis_url = current_app.config.get("REDIS_URL", "redis://redis:6379/0")

    def _listen():
        r = redis.from_url(redis_url)
        ps = r.pubsub()
        ps.subscribe(SSE_CHANNEL)
        for msg in ps.listen():
            if msg["type"] != "message":
                continue
            payload = msg["data"]
            if isinstance(payload, bytes):
                payload = payload.decode()
            with _sse_lock:
                dead = []
                for q in _sse_listeners:
                    try:
                        q.put_nowait(payload)
                    except queue.Full:
                        dead.append(q)
                for q in dead:
                    _sse_listeners.remove(q)

    t = threading.Thread(target=_listen, daemon=True)
    t.start()


incident_bp = Blueprint(
    "incident",
    __name__,
    template_folder="templates",
    url_prefix="/incidents",
)


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@incident_bp.get("/")
def list_incidents():
    """Return all incidents ordered by most recent first."""
    incidents = (
        Incident.query.order_by(Incident.detected_at.desc()).all()
    )
    return jsonify([i.to_dict() for i in incidents])


@incident_bp.get("/<int:incident_id>")
def get_incident(incident_id: int):
    """Return a single incident by ID."""
    incident = db.session.get(Incident, incident_id)
    if incident is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(incident.to_dict())


@incident_bp.post("/")
def create_incident():
    """Record a new incident (called by the AWS log-monitoring service).

    This only **records** the incident — it does NOT auto-run RAG analysis.
    The AWS service should call ``POST /incidents/<id>/analyze`` separately
    after recording.

    Expects JSON body::

        {
            "error_code": "FAULT_SQL_INJECTION_TEST",
            "symptoms": "invalid SQL executed on /test-fault/run",
            "breadcrumbs": ["invalid_sql_executed"],
            "metrics": {"error_rate": 1.0}
        }
    """
    data = request.get_json(force=True, silent=True) or {}
    error_code = data.get("error_code", "UNKNOWN")
    symptoms = data.get("symptoms", "")
    breadcrumbs = data.get("breadcrumbs", [])
    metrics = data.get("metrics")

    incident = record_incident(
        error_code=error_code,
        symptoms=symptoms,
        breadcrumbs=breadcrumbs,
        metrics=metrics,
    )

    _publish_event({
        "type": "created",
        "incident": incident.to_dict(),
    })

    return jsonify(incident.to_dict()), 201


@incident_bp.post("/<int:incident_id>/analyze")
def reanalyze_incident(incident_id: int):
    """Run RAG analysis on an existing incident."""
    incident = db.session.get(Incident, incident_id)
    if incident is None:
        return jsonify({"error": "not found"}), 404

    incident = analyze_incident(incident)

    _publish_event({
        "type": "analyzed",
        "incident": incident.to_dict(),
    })

    return jsonify(incident.to_dict())


@incident_bp.post("/<int:incident_id>/agent-plan")
def build_plan(incident_id: int):
    """Build an approval-gated agent remediation plan for an incident."""
    incident = db.session.get(Incident, incident_id)
    if incident is None:
        return jsonify({"error": "not found"}), 404

    plan = build_agent_plan(incident)
    return jsonify(plan)


@incident_bp.post("/<int:incident_id>/agent-execute")
def execute_plan(incident_id: int):
    """Approve and prepare execution payload for the selected playbook."""
    incident = db.session.get(Incident, incident_id)
    if incident is None:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    auto_approve = bool(
        current_app.config.get("AGENT_AUTO_REMEDIATE", True)
    )
    approved = bool(data.get("approve", auto_approve))

    plan = build_agent_plan(incident)
    result = approve_plan(plan, approved=approved)

    if result["status"] == "approved_for_pipeline":
        incident.remediation = plan["selected_action"]["summary"]
        if not incident.root_cause:
            incident.root_cause = plan["evidence"].get("rag_summary")
        db.session.add(incident)
        db.session.commit()

    status_code = 200 if result["status"] == "approved_for_pipeline" else 400
    return jsonify(result), status_code


@incident_bp.post("/<int:incident_id>/resolve")
def resolve(incident_id: int):
    """Mark an incident as resolved and index it in Backboard.

    Expects JSON body::

        {
            "root_cause": "Connection pool exhausted due to pg_sleep",
            "remediation": "Restarted connection pool, added timeout",
            "verification": "Error rate dropped to 0%"
        }
    """
    incident = db.session.get(Incident, incident_id)
    if incident is None:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    incident = resolve_incident(
        incident,
        root_cause=data.get("root_cause", ""),
        remediation=data.get("remediation", ""),
        verification=data.get("verification", ""),
        resolved=data.get("resolved", True),
    )

    _publish_event({
        "type": "resolved",
        "incident": incident.to_dict(),
    })

    return jsonify(incident.to_dict())


@incident_bp.post("/setup-assistant")
def bootstrap_assistant():
    """One-time: create the Backboard RAG assistant and thread.

    Returns the ``assistant_id`` and ``thread_id`` which should be stored
    in your ``.env`` as ``BACKBOARD_ASSISTANT_ID`` and
    ``BACKBOARD_THREAD_ID``.
    """
    result = setup_assistant()
    return jsonify({
        "assistant_id": result["assistant_id"],
        "thread_id": result["thread_id"],
        "message": (
            "Save assistant_id as BACKBOARD_ASSISTANT_ID and "
            "thread_id as BACKBOARD_THREAD_ID in your .env"
        ),
    }), 201


@incident_bp.post("/seed-kb")
def seed_kb():
    """Seed the Backboard RAG knowledge base with example incidents.

    Uploads 15 pre-written resolved incident documents (5 per error type)
    so the RAG pipeline has historical context to draw from.
    """
    try:
        results = seed_knowledge_base()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    ok = [r for r in results if r.get("document_id")]
    failed = [r for r in results if not r.get("document_id")]

    return jsonify({
        "uploaded": len(ok),
        "failed": len(failed),
        "results": results,
    }), 201


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------

@incident_bp.get("/stream")
def sse_stream():
    """Server-Sent Events stream for live dashboard updates.

    The browser opens an ``EventSource`` to this endpoint and receives
    JSON events whenever an incident is created, analyzed, or resolved.
    Events are distributed via Redis pub/sub so this works across all
    gunicorn workers.
    """
    _start_subscriber_thread()

    def _generate():
        q: queue.Queue = queue.Queue(maxsize=64)
        with _sse_lock:
            _sse_listeners.append(q)
        try:
            while True:
                try:
                    payload = q.get(timeout=25)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if q in _sse_listeners:
                    _sse_listeners.remove(q)

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

@incident_bp.get("/dashboard")
def dashboard():
    """Render the real-time incident dashboard."""
    incidents = (
        Incident.query.order_by(Incident.detected_at.desc()).all()
    )
    return render_template("incident/dashboard.html", incidents=incidents)
