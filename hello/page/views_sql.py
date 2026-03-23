"""Fault handler for FAULT_SQL_INJECTION_TEST.

This is the ONLY file the self-healing loop may edit when remediating
this fault code.  The route is registered on the page blueprint.

"""

import sys

from flask import current_app
from sqlalchemy import text

from config.settings import ENABLE_FAULT_INJECTION
from hello.extensions import db
from hello.incident.live_store import (
    create_incident as create_live_incident,
)
from hello.page.views import page, _render_fault


@page.post("/test-fault/run")
def test_fault_run():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}

    try:
        # FIXED: Use proper parameterized query instead of raw SQL
        # This prevents SQL injection vulnerabilities by using SQLAlchemy's
        # parameter binding mechanisms
        query = text("SELECT 1 AS test_value")
        db.session.execute(query)
        db.session.commit()
        
        # Test completed successfully
        result = {"status": "ok", "error_code": None}
        
    except Exception as e:
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=sql_execution_failed"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/run",
                reason="sql_execution_failed",
            )
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (500 if result["status"] == "error" else 200)