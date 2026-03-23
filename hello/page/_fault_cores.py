"""Stable route wrappers for fault injection endpoints.

These routes NEVER change — they are NOT touched by the self-healing loop.
Each route delegates to the corresponding function in views_sql.py,
views_api.py, or views_db.py.  If the target module failed to import
(e.g. due to a bad self-healing fix), the wrapper returns HTTP 500 with
a clear error so CloudWatch still picks up the fault code.
"""

import sys

from flask import current_app

from hello.incident.live_store import (
    create_incident as create_live_incident,
)
from hello.page.views import page, _render_fault


def _handle_fault_route_failure(
    *,
    fault_code: str,
    route: str,
    exc: Exception,
):
    reason = "fault_handler_unavailable"
    msg = (
        f"{fault_code} route={route} reason={reason} "
        f"error={exc.__class__.__name__}:{exc}"
    )
    print(msg, file=sys.stderr)
    current_app.logger.exception(msg)

    try:
        create_live_incident(
            error_code=fault_code,
            route=route,
            reason=reason,
        )
    except Exception:
        current_app.logger.exception("Failed to create incident for %s", fault_code)

    return _render_fault({"status": "error", "error_code": fault_code}), 500


# ─── SQL injection fault ────────────────────────────────────────────
@page.post("/test-fault/run")
def _route_test_fault_run():
    try:
        from hello.page.views_sql import test_fault_run

        return test_fault_run()
    except Exception as exc:
        return _handle_fault_route_failure(
            fault_code="FAULT_SQL_INJECTION_TEST",
            route="/test-fault/run",
            exc=exc,
        )


# ─── External API latency fault ─────────────────────────────────────
@page.post("/test-fault/external-api")
def _route_test_fault_external_api():
    try:
        from hello.page.views_api import test_fault_external_api

        return test_fault_external_api()
    except Exception as exc:
        return _handle_fault_route_failure(
            fault_code="FAULT_EXTERNAL_API_LATENCY",
            route="/test-fault/external-api",
            exc=exc,
        )


# ─── DB timeout fault ───────────────────────────────────────────────
@page.post("/test-fault/db-timeout")
def _route_test_fault_db_timeout():
    try:
        from hello.page.views_db import test_fault_db_timeout

        return test_fault_db_timeout()
    except Exception as exc:
        return _handle_fault_route_failure(
            fault_code="FAULT_DB_TIMEOUT",
            route="/test-fault/db-timeout",
            exc=exc,
        )
