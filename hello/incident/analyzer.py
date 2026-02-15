"""Incident Analyzer – detects faults and feeds the RAG pipeline.

This module bridges the fault-injection endpoints (and future real
monitoring hooks) with the Backboard RAG service.  It:

1. Captures fault signals (error codes, symptoms, breadcrumbs).
2. Persists a new ``Incident`` record.
3. Sends the incident through ``rag_service.analyze_and_store`` to retrieve
   similar past incidents and LLM-generated root-cause suggestions.
4. Optionally indexes resolved incidents back into Backboard for future use.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from hello.extensions import db
from hello.incident.models import Incident

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fault → Incident creation
# ---------------------------------------------------------------------------

def record_incident(
    error_code: str,
    symptoms: str,
    breadcrumbs: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> Incident:
    """Create and persist a new Incident from a detected fault.

    Parameters
    ----------
    error_code:
        Machine-readable fault identifier, e.g. ``FAULT_SQL_INJECTION_TEST``.
    symptoms:
        Human-readable description of what was observed.
    breadcrumbs:
        List of log markers / trace breadcrumbs.
    metrics:
        Optional metric snapshot (error_rate, latency, etc.).
    """
    bc = json.dumps(breadcrumbs or [])

    incident = Incident(
        error_code=error_code,
        symptoms=symptoms,
        breadcrumbs=bc,
        detected_at=datetime.datetime.utcnow(),
    )
    db.session.add(incident)
    db.session.commit()

    logger.info(
        "Recorded incident id=%s code=%s",
        incident.id,
        incident.error_code,
    )
    return incident


# ---------------------------------------------------------------------------
# RAG-powered analysis
# ---------------------------------------------------------------------------

def analyze_incident(incident: Incident) -> Incident:
    """Run the full RAG analysis pipeline on an incident.

    Queries Backboard for similar past incidents, stores the retrieval
    results, and populates ``root_cause`` with the LLM suggestion.
    """
    from hello.incident.rag_service import analyze_and_store

    return analyze_and_store(incident, db.session)


# ---------------------------------------------------------------------------
# Post-remediation: index the resolved incident for future retrieval
# ---------------------------------------------------------------------------

def resolve_incident(
    incident: Incident,
    root_cause: str,
    remediation: str,
    verification: str = "",
    resolved: bool = True,
) -> Incident:
    """Mark an incident as resolved and index it in Backboard.

    After a human or automated process has resolved the incident, call
    this to persist the outcome and feed it back into the RAG knowledge
    base so future incidents can benefit.
    """
    incident.root_cause = root_cause
    incident.remediation = remediation
    incident.verification = verification
    incident.resolved = resolved

    db.session.add(incident)
    db.session.commit()

    # Index into Backboard asynchronously (best-effort).
    from hello.incident.rag_service import index_incident

    doc_id = index_incident(incident)
    if doc_id:
        incident.backboard_doc_id = doc_id
        db.session.add(incident)
        db.session.commit()

    logger.info(
        "Resolved incident id=%s, indexed as doc=%s",
        incident.id,
        doc_id,
    )
    return incident


# ---------------------------------------------------------------------------
# Convenience: record + analyse in one call
# ---------------------------------------------------------------------------

def detect_and_analyze(
    error_code: str,
    symptoms: str,
    breadcrumbs: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> Incident:
    """Record a fault and immediately run RAG analysis.

    This is the primary entry-point for the fault-injection views and
    future monitoring hooks.
    """
    incident = record_incident(
        error_code=error_code,
        symptoms=symptoms,
        breadcrumbs=breadcrumbs,
        metrics=metrics,
    )

    try:
        incident = analyze_incident(incident)
    except Exception:
        logger.exception(
            "RAG analysis failed for incident %s – continuing without",
            incident.id,
        )

    return incident
