"""Stores the original faulty code for each fault file for the reset functionality.

When the self-healing loop fixes a fault file and deploys it, the "Reset All"
button uses these templates to restore the original faulty code and redeploy,
enabling the demo cycle to repeat.

Each fault file has its own template so resets are truly independent.
"""

FAULTY_FAULT_SQL_CONTENT = '''\
"""Fault handler for FAULT_SQL_INJECTION_TEST.

This file is the ONLY file the self-healing loop may edit when remediating
this fault code.  The stable route wrapper in _fault_cores.py delegates here.
"""

import sys

from flask import current_app
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

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/run",
                reason="invalid_sql_executed",
            )
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (500 if result["status"] == "error" else 200)
'''

FAULTY_FAULT_API_CONTENT = '''\
"""Fault handler for FAULT_EXTERNAL_API_LATENCY.

This file is the ONLY file the self-healing loop may edit when remediating
this fault code.  The stable route wrapper in _fault_cores.py delegates here.
"""

import os
import sys
import time

import requests
from flask import current_app

from config.settings import ENABLE_FAULT_INJECTION
from hello.incident.live_store import (
    create_incident as create_live_incident,
)
from hello.page.views import _render_fault


def _record_external_api_incident(reason: str, latency: float) -> None:
    """Persist a live incident for the external API fault route."""
    create_live_incident(
        error_code="FAULT_EXTERNAL_API_LATENCY",
        route="/test-fault/external-api",
        reason=reason,
        latency=latency,
    )


def test_fault_external_api():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}
    mock_api_base_url = os.getenv("MOCK_API_BASE_URL", "http://mock_api:5001").rstrip("/")

    start = time.time()

    try:
        # INTENTIONAL BUG: 3s timeout against mock API with a high chance of
        # latency or malformed data.
        r = requests.get(f"{mock_api_base_url}/data", timeout=3)
        latency = time.time() - start
        current_app.logger.info(f"external_call_latency={latency:.2f}")
        r.raise_for_status()
        payload = r.json()
        if payload.get("value") != 42:
            result = {
                "status": "error",
                "error_code": error_code,
                "detail": "wrong_data",
                "latency": f"{latency:.2f}s",
                "data": payload,
            }
            msg = (
                f"{error_code} route=/test-fault/external-api "
                f"reason=wrong_data latency={latency:.2f}"
            )
            print(msg, file=sys.stderr)
            current_app.logger.error(msg)

            try:
                _record_external_api_incident("wrong_data", latency)
            except Exception:
                current_app.logger.exception("Failed to create incident for %s", error_code)

            return _render_fault(result), 504

        result = {
            "status": "ok",
            "error_code": None,
            "data": payload,
            "latency": f"{latency:.2f}s",
        }

    except requests.exceptions.Timeout:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "timeout",
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=external_timeout latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            _record_external_api_incident("external_timeout", latency)
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    except requests.exceptions.HTTPError:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "upstream_500",
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=upstream_failure latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            _record_external_api_incident("upstream_failure", latency)
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    except requests.exceptions.ConnectionError:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "connection_refused",
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=connection_error latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            _record_external_api_incident("connection_error", latency)
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (504 if result["status"] == "error" else 200)
'''

FAULTY_FAULT_DB_CONTENT = '''\
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
    # With a real DB, pg_sleep(10) + statement_timeout='5500ms' takes ~5.5s.
    # If the DB is unreachable the connection error is instant, so we
    # enforce a floor so the response always visibly hangs.
    min_delay = 5.0

    try:
        db.session.execute(text("SET LOCAL statement_timeout = \\'5500ms\\';"))
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
'''

# Map fault codes to their template content and target file path
FAULT_FILE_MAP = {
    "FAULT_SQL_INJECTION_TEST": {
        "file_path": "hello/page/fault_sql.py",
        "content": FAULTY_FAULT_SQL_CONTENT,
    },
    "FAULT_EXTERNAL_API_LATENCY": {
        "file_path": "hello/page/fault_api.py",
        "content": FAULTY_FAULT_API_CONTENT,
    },
    "FAULT_DB_TIMEOUT": {
        "file_path": "hello/page/fault_db.py",
        "content": FAULTY_FAULT_DB_CONTENT,
    },
}
