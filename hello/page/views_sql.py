"""Fault handler for FAULT_SQL_INJECTION_TEST.

This is the ONLY file the self-healing loop may edit when remediating
this fault code.  The route is registered on the page blueprint.

"""

import sys

from flask import current_app, request
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
        # SECURITY FIX: Use parameterized query to prevent SQL injection
        # Instead of concatenating user input directly into SQL query
        test_param = request.form.get('test_param', '1')
        
        # Validate input to ensure it's safe
        if not test_param.isdigit():
            test_param = '1'
        
        # Use parameterized query with proper escaping
        query = text("SELECT :param as test_value")
        db.session.execute(query, {"param": int(test_param)})
        db.session.commit()
        
    except Exception as e:
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=sql_injection_prevented"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/run",
                reason="sql_injection_prevented",
            )
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (500 if result["status"] == "error" else 200)