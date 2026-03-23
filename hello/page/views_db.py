"""Fault handler for FAULT_DB_TIMEOUT.

This file is the ONLY file the self-healing loop may edit when remediating
this fault code.  The stable route wrapper in _fault_cores.py delegates here.
"""

import sys
import time

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


def test_fault_db_timeout():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_DB_TIMEOUT"
    result = {"status": "ok", "error_code": None}

    start = time.time()

    # INTENTIONAL BUG: minimum 5s delay to simulate a slow DB timeout.
    # With a real DB, pg_sleep(10) + statement_timeout='5500ms' takes ~5.5s.
    # If the DB is unreachable the connection error is instant, so we
    # enforce a floor so the response always visibly hangs.
    min_delay = 5.0

    try:
        db.session.execute(text("SET LOCAL statement_timeout = '5500ms';"))
        db.session.execute(text("SELECT pg_sleep(10);"))
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

        if not _is_verification_probe():
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
