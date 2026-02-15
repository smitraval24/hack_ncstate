"""Deterministic agent workflow built on top of RAG incident analysis.

This module keeps the hackathon implementation simple:
- Only supports the 3 injected fault types.
- Uses stored RAG output as evidence.
- Selects one predefined remediation action per fault.
- Requires explicit approval before execution.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ActionSpec:
    """Static remediation action for a known fault."""

    action_id: str
    script_path: str
    summary: str
    verification_hint: str


PLAYBOOKS: dict[str, ActionSpec] = {
    "FAULT_SQL_INJECTION_TEST": ActionSpec(
        action_id="fix_fault_sql_injection",
        script_path="scripts/remediation/fix_fault_sql_injection.sh",
        summary=(
            "Wrap faulty SQL path with controlled exception handling and "
            "explicit transaction rollback."
        ),
        verification_hint=(
            "Re-run /test-fault/run and verify no open transaction leakage "
            "in logs/metrics."
        ),
    ),
    "FAULT_EXTERNAL_API_LATENCY": ActionSpec(
        action_id="fix_fault_external_api_latency",
        script_path="scripts/remediation/fix_fault_external_api_latency.sh",
        summary=(
            "Enable timeout-aware retries with backoff and degraded fallback "
            "for upstream failures."
        ),
        verification_hint=(
            "Re-run /test-fault/external-api and verify latency/error rate "
            "returns within threshold."
        ),
    ),
    "FAULT_DB_TIMEOUT": ActionSpec(
        action_id="fix_fault_db_timeout",
        script_path="scripts/remediation/fix_fault_db_timeout.sh",
        summary=(
            "Apply DB timeout/pool hardening and reduce long-running test "
            "query blast radius."
        ),
        verification_hint=(
            "Re-run /test-fault/db-timeout concurrently and verify pool "
            "stability and healthy /up checks."
        ),
    ),
}


KEYWORDS: dict[str, tuple[str, ...]] = {
    "FAULT_SQL_INJECTION_TEST": (
        "invalid sql",
        "syntax error",
        "programmingerror",
        "rollback",
    ),
    "FAULT_EXTERNAL_API_LATENCY": (
        "timeout",
        "upstream",
        "connectionerror",
        "latency",
        "retry",
    ),
    "FAULT_DB_TIMEOUT": (
        "pg_sleep",
        "queuepool",
        "pool exhaustion",
        "statement_timeout",
        "db timeout",
    ),
}


def _parse_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _extract_rag_payload(incident: Any) -> dict[str, Any]:
    payload = _parse_json(incident.rag_response, {})
    if not isinstance(payload, dict):
        return {}
    return payload


def _infer_fault_code(incident: Any, rag_payload: dict[str, Any]) -> str | None:
    if incident.error_code in PLAYBOOKS:
        return incident.error_code

    content = str(rag_payload.get("content", "")).lower()
    for fault_code, keywords in KEYWORDS.items():
        if any(keyword in content for keyword in keywords):
            return fault_code

    return None


def _compute_confidence(incident: Any, rag_payload: dict[str, Any], fault_code: str | None) -> float:
    if not fault_code:
        return 0.0

    score = 0.0
    if incident.error_code == fault_code:
        score += 0.65
    else:
        score += 0.35

    memories = rag_payload.get("retrieved_memories") or []
    files = rag_payload.get("retrieved_files") or []
    if memories:
        score += 0.15
    if files:
        score += 0.1

    breadcrumbs = _parse_json(getattr(incident, "breadcrumbs", None), [])
    if isinstance(breadcrumbs, list) and breadcrumbs:
        lower_breadcrumbs = " ".join(str(x).lower() for x in breadcrumbs)
        keywords = KEYWORDS.get(fault_code, ())
        if any(keyword in lower_breadcrumbs for keyword in keywords):
            score += 0.1

    content = str(rag_payload.get("content", "")).lower()
    if any(keyword in content for keyword in KEYWORDS.get(fault_code, ())):
        score += 0.1

    return round(min(score, 0.99), 2)


def build_agent_plan(incident: Any) -> dict[str, Any]:
    """Build an approval-gated remediation plan from incident + RAG data."""
    rag_payload = _extract_rag_payload(incident)
    fault_code = _infer_fault_code(incident, rag_payload)
    confidence = _compute_confidence(incident, rag_payload, fault_code)

    action: ActionSpec | None = PLAYBOOKS.get(fault_code) if fault_code else None

    if action:
        decision = "ready_for_approval"
        selected_action: dict[str, Any] = {
            "fault_code": fault_code,
            "action_id": action.action_id,
            "script_path": action.script_path,
            "summary": action.summary,
            "verification_hint": action.verification_hint,
        }
    else:
        decision = "manual_triage_required"
        selected_action = {
            "fault_code": None,
            "action_id": "manual_triage",
            "script_path": None,
            "summary": "No deterministic playbook matched this incident.",
            "verification_hint": "Escalate to on-call and attach logs + RAG output.",
        }

    evidence = {
        "rag_summary": rag_payload.get("content", "") or "",
        "retrieved_memory_count": len(rag_payload.get("retrieved_memories") or []),
        "retrieved_file_count": len(rag_payload.get("retrieved_files") or []),
    }

    return {
        "incident_id": incident.id,
        "workflow": [
            "detect",
            "retrieve_context",
            "propose_fix",
            "await_approval",
            "execute_playbook",
            "verify_health",
        ],
        "decision": decision,
        "requires_approval": True,
        "confidence": confidence,
        "selected_action": selected_action,
        "evidence": evidence,
    }


def approve_plan(plan: dict[str, Any], approved: bool) -> dict[str, Any]:
    """Return an execution payload once a human approval flag is provided."""
    if not approved:
        return {
            "status": "approval_required",
            "message": "Set approve=true to execute the selected remediation playbook.",
            "plan": plan,
        }

    action = plan.get("selected_action", {})
    if action.get("action_id") == "manual_triage":
        return {
            "status": "blocked",
            "message": "No safe playbook available; manual triage required.",
            "plan": plan,
        }

    return {
        "status": "approved_for_pipeline",
        "message": "Execute the selected remediation script in CI/CD, then run verification checks.",
        "execution": {
            "action_id": action.get("action_id"),
            "script_path": action.get("script_path"),
            "verification_hint": action.get("verification_hint"),
        },
        "plan": plan,
    }


def execute_approved_action(
    approved_payload: dict[str, Any],
    project_root: str | Path,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Execute an approved remediation action script from the allow-list."""
    if approved_payload.get("status") != "approved_for_pipeline":
        return {
            "status": "blocked",
            "message": "Execution payload is not approved.",
            "execution_result": None,
        }

    execution = approved_payload.get("execution", {})
    script_path = execution.get("script_path")
    allowed_paths = {spec.script_path for spec in PLAYBOOKS.values()}
    if script_path not in allowed_paths:
        return {
            "status": "blocked",
            "message": "Script path is not in the approved playbook allow-list.",
            "execution_result": None,
        }

    root = Path(project_root).resolve()
    script_abs = (root / script_path).resolve()
    try:
        script_abs.relative_to(root)
    except ValueError:
        return {
            "status": "blocked",
            "message": "Script path resolves outside the project root.",
            "execution_result": None,
        }

    if not script_abs.exists():
        return {
            "status": "failed",
            "message": f"Script not found: {script_path}",
            "execution_result": None,
        }

    completed = subprocess.run(
        ["bash", str(script_abs)],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )

    status = "executed" if completed.returncode == 0 else "failed"
    return {
        "status": status,
        "message": (
            "Remediation playbook executed."
            if status == "executed"
            else "Remediation playbook failed."
        ),
        "execution_result": {
            "action_id": execution.get("action_id"),
            "script_path": script_path,
            "return_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "verification_hint": execution.get("verification_hint"),
        },
    }
