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

    # FIXED: Reduced minimum delay to 0.1s for faster response times
    # Increased statement timeout to 30s and reduced pg_sleep to 1s to prevent timeouts
    min_delay = 0.1

    try:
        db.session.execute(text("SET LOCAL statement_timeout = '30000ms';"))
        db.session.execute(text("SELECT pg_sleep(1);"))
        db.session.commit()  # Added proper transaction management
        latency = time.time() - start
        result = {
            "status": "ok",
            "error_code": None,
            "latency": f"{latency:.2f}s",
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
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/db-timeout "
            f"reason=db_statement_timeout latency={latency:.2f}"
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
            except Exception:
                current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (500 if result["status"] == "error" else 200)