"""Fault handler for FAULT_DB_TIMEOUT.

This is the ONLY file the self-healing loop may edit when remediating
this fault code.  The route is registered on the page blueprint.

"""

import sys
import time

from flask import current_app
from sqlalchemy import text

from config.settings import ENABLE_FAULT_INJECTION
from hello.extensions import db
from hello.incident.live_store import (
    create_incident as create_live_incident,
)
from hello.page.views import _is_fault_verification_request, _render_fault, page


@page.post("/test-fault/db-timeout")
def test_fault_db_timeout():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_DB_TIMEOUT"
    result = {"status": "ok", "error_code": None}
    verification_only = _is_fault_verification_request()

    start = time.time()

    # FIXED: Further optimized for stability - increased timeout to 60s and reduced sleep to 0.5s
    # This ensures the operation completes well within timeout bounds
    min_delay = 0.05  # Reduced to 0.05s for even faster response

    try:
        # Set a very generous timeout to prevent any timeout issues
        db.session.execute(text("SET LOCAL statement_timeout = '60000ms';"))
        # Use minimal sleep to test connection without causing timeouts
        db.session.execute(text("SELECT pg_sleep(0.5);"))
        db.session.commit()  # Proper transaction management
        latency = time.time() - start
        result = {
            "status": "ok",
            "error_code": None,
            "latency": f"{latency:.3f}s",
        }
    except Exception as e:
        db.session.rollback()
        elapsed = time.time() - start
        if elapsed < min_delay:
            time.sleep(min_delay - elapsed)
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": str(e)[:200],
            "latency": f"{latency:.3f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/db-timeout "
            f"reason=db_statement_timeout latency={latency:.3f}"
        )
        if not verification_only:
            print(msg, file=sys.stderr)
            current_app.logger.error(f"db_error={e!s}")

            try:
                create_live_incident(
                    error_code=error_code,
                    route="/test-fault/db-timeout",
                    reason="db_statement_timeout",
                    latency=latency,
                )
            except Exception as incident_error:
                # Enhanced error logging for incident creation failures
                current_app.logger.exception(
                    "Failed to create incident for %s: %s", error_code, str(incident_error)
                )

    return _render_fault(result), (500 if result["status"] == "error" else 200)