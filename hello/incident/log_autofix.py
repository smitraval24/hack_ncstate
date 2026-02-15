"""Log-driven auto-remediation for known injected faults.

When a fault log line is emitted, this module parses it and runs the
RAG-backed remediation workflow asynchronously.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hello.incident.agent_workflow import (
    approve_plan,
    build_agent_plan,
    execute_approved_action,
)
from hello.incident.analyzer import detect_and_analyze, resolve_incident


@dataclass(frozen=True)
class FaultEvent:
    error_code: str
    route: str
    reason: str
    latency: str | None = None


def parse_fault_log(message: str) -> FaultEvent | None:
    """Parse structured fault log lines into a typed event."""
    if "FAULT_" not in message:
        return None

    parts = message.split()
    if not parts:
        return None

    error_code = parts[0]
    if not error_code.startswith("FAULT_"):
        return None

    fields: dict[str, str] = {}
    for token in parts[1:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        fields[key] = value

    route = fields.get("route", "")
    reason = fields.get("reason", "")
    latency = fields.get("latency")
    if not route:
        return None

    return FaultEvent(
        error_code=error_code,
        route=route,
        reason=reason,
        latency=latency,
    )


def _autofix_inputs(event: FaultEvent) -> tuple[str, list[str], dict[str, Any]]:
    if event.error_code == "FAULT_SQL_INJECTION_TEST":
        return (
            "Invalid SQL executed on /test-fault/run",
            ["invalid_sql_executed", "test_fault_endpoint"],
            {"route": event.route, "reason": event.reason},
        )
    if event.error_code == "FAULT_EXTERNAL_API_LATENCY":
        reason = event.reason or "external_failure"
        detail = {
            "external_timeout": "timeout",
            "upstream_failure": "upstream_500",
            "connection_error": "connection_refused",
        }.get(reason, reason)
        return (
            f"External API failure on {event.route}",
            ["external_api_call", detail],
            {
                "route": event.route,
                "reason": reason,
                "latency": event.latency,
            },
        )
    if event.error_code == "FAULT_DB_TIMEOUT":
        return (
            "DB timeout or pool exhaustion on /test-fault/db-timeout",
            ["pg_sleep_executed", "queue_pool_limit"],
            {
                "route": event.route,
                "reason": event.reason,
                "latency": event.latency,
            },
        )
    return ("Unknown fault", [], {"route": event.route, "reason": event.reason})


def run_autofix_for_event(app: Any, event: FaultEvent) -> dict[str, Any]:
    """Run the full auto-remediation workflow for a parsed fault event."""
    symptoms, breadcrumbs, metrics = _autofix_inputs(event)
    incident = detect_and_analyze(
        error_code=event.error_code,
        symptoms=symptoms,
        breadcrumbs=breadcrumbs,
        metrics=metrics,
    )

    plan = build_agent_plan(incident)
    approved = approve_plan(plan, approved=True)
    project_root = Path(app.root_path).parent
    execution = execute_approved_action(approved, project_root=project_root)

    resolved = execution.get("status") == "executed"
    verification = (
        execution.get("execution_result", {}).get("verification_hint")
        or execution.get("message", "")
    )
    root_cause = (
        plan.get("evidence", {}).get("rag_summary")
        or incident.root_cause
        or f"Auto-detected {event.error_code}"
    )
    remediation = plan.get("selected_action", {}).get("summary") or ""

    resolve_incident(
        incident,
        root_cause=root_cause,
        remediation=remediation,
        verification=verification,
        resolved=resolved,
    )

    return {
        "incident_id": incident.id,
        "decision": plan.get("decision"),
        "action": plan.get("selected_action", {}).get("action_id"),
        "execution_status": execution.get("status"),
    }


class FaultLogAutoFixHandler(logging.Handler):
    """Async log handler that triggers auto-remediation for fault log lines."""

    def __init__(self, app: Any, dedupe_window_seconds: float = 2.0):
        super().__init__(level=logging.ERROR)
        self._app = app
        self._dedupe_window_seconds = dedupe_window_seconds
        self._recent: dict[str, float] = {}
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event = parse_fault_log(record.getMessage())
            if event is None:
                return

            dedupe_key = f"{event.error_code}:{event.route}:{event.reason}"
            now = time.monotonic()
            with self._lock:
                last_seen = self._recent.get(dedupe_key, 0.0)
                if now - last_seen < self._dedupe_window_seconds:
                    return
                self._recent[dedupe_key] = now

            thread = threading.Thread(
                target=self._process,
                args=(event,),
                daemon=True,
            )
            thread.start()
        except Exception:
            self.handleError(record)

    def _process(self, event: FaultEvent) -> None:
        with self._app.app_context():
            try:
                result = run_autofix_for_event(self._app, event)
                self._app.logger.info("autofix completed: %s", result)
            except Exception:
                self._app.logger.exception(
                    "autofix failed for error_code=%s route=%s",
                    event.error_code,
                    event.route,
                )


def register_fault_log_autofix(app: Any) -> None:
    """Register the log-triggered auto-remediator once per app instance."""
    if not bool(app.config.get("AGENT_AUTO_REMEDIATE", True)):
        return

    if app.extensions.get("fault_log_autofix_registered"):
        return

    handler = FaultLogAutoFixHandler(app)
    app.logger.addHandler(handler)
    app.extensions["fault_log_autofix_registered"] = True

