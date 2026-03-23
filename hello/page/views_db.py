"""Fault handler for FAULT_DB_TIMEOUT.

This file is the ONLY file the self-healing loop may edit when remediating
this fault code.  The stable route wrapper in _fault_cores.py delegates here.
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
from hello.page.views import _render_fault


def test_fault_db_timeout():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_DB_TIMEOUT"
    result = {"status": "ok", "error_code": None}

    start = time.time()

    # INTENTIONAL BUG: minimum 5s delay to simulate a slow DB timeout.
    # With a real DB, pg_sleep(10) + statement_timeout='12000ms' takes ~10s.
    # If the DB is unreachable the connection error is instant, so we
    # enforce a floor so the response always visibly hangs.
    min_delay = 5.0

    try:
        # Fix: Increased timeout from 5500ms to 12000ms (12 seconds) to accommodate
        # the 10-second pg_sleep(10) operation. This prevents timeout errors while
        # maintaining fault injection testing capability.
        db.session.execute(text("SET LOCAL statement_timeout = '12000ms';"))
        db.session.execute(text("SELECT pg_sleep(10);"))
        
        # Commit the transaction after successful execution to ensure proper
        # transaction handling and prevent any potential connection issues
        db.session.commit()
        
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