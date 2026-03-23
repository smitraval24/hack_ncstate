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


def test_fault_run():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}

    try:
        # FIXED: Use parameterized query to prevent SQL injection
        # This demonstrates the proper way to handle user input in SQL queries
        user_input = request.args.get('search', 'test')
        
        # Safe parameterized query - prevents SQL injection
        safe_query = text("SELECT :input_param as search_term")
        db.session.execute(safe_query, {"input_param": user_input})
        
        result = {
            "status": "ok", 
            "error_code": None,
            "message": f"SQL injection test passed - safely handled input: {user_input}"
        }

    except Exception as e:
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=sql_query_failed error={str(e)}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/run",
                reason="sql_query_failed",
            )
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (500 if result["status"] == "error" else 200)