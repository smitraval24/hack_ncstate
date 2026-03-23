"""Fault handler for FAULT_SQL_INJECTION_TEST.

This file is the ONLY file the self-healing loop may edit when remediating
this fault code.  The stable route wrapper in _fault_cores.py delegates here.
"""

import sys

from flask import current_app, request
from sqlalchemy import text

from config.settings import ENABLE_FAULT_INJECTION
from hello.extensions import db
from hello.incident.live_store import (
    create_incident as create_live_incident,
)
from hello.page.views import _render_fault


def _is_verification_probe() -> bool:
    return request.headers.get("X-Fault-Verification") == "1"


def test_fault_run():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}

    try:
        # INTENTIONAL BUG: malformed SQL that always fails with a syntax error
        db.session.execute(text("SELECT FROM"))
    except Exception as e:
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=invalid_sql_executed"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        if not _is_verification_probe():
            try:
                create_live_incident(
                    error_code=error_code,
                    route="/test-fault/run",
                    reason="invalid_sql_executed",
                )
            except Exception:
                current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (500 if result["status"] == "error" else 200)
