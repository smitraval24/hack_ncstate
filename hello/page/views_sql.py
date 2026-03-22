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
from hello.page.views import _is_fault_verification_request, _render_fault, page


@page.post("/test-fault/run")
def test_fault_run():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}
    verification_only = _is_fault_verification_request()

    try:
        # Get test parameter from request, validate and sanitize input
        test_param = request.form.get('test_param', '1')
        
        # Validate that test_param is numeric to prevent injection
        if not test_param.isdigit():
            test_param = '1'  # Default to safe value if input is invalid
        
        # Convert to integer for type safety
        test_param_int = int(test_param)
        
        # Use parameterized query with SQLAlchemy's text() function and parameter binding
        # This prevents SQL injection by separating SQL code from data
        query = text("SELECT :param AS test_value")
        result_set = db.session.execute(query, {"param": test_param_int})
        db.session.commit()  # Commit successful execution
        
    except Exception:
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        if not verification_only:
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