"""This file handles the views logic for the page part of the project."""

import os
import sys
import time
from importlib.metadata import version

import requests
from flask import Blueprint, current_app, render_template
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from config.settings import DEBUG, ENABLE_FAULT_INJECTION
from hello.extensions import db
from hello.incident.live_store import (
    create_incident as create_live_incident,
)

# This blueprint groups related routes for this part of the app.
page = Blueprint("page", __name__, template_folder="templates")

PYTHON_VER = os.environ.get("PYTHON_VERSION", sys.version.split()[0])
BUILD_SHA = os.environ.get("BUILD_SHA", "").strip()


# This function handles the home work for this file.
@page.get("/")
def home():
    return render_template(
        "page/home.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=ENABLE_FAULT_INJECTION,
    )


def _render_fault(result=None):
    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        build_sha=(BUILD_SHA[:7] if BUILD_SHA else "local"),
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    )


@page.get("/test-fault")
def test_fault():
    return _render_fault()


def test_fault_run():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}

    try:
        # FIXED: Replaced vulnerable raw SQL execution with safer error simulation
        # This simulates a SQL error without using potentially exploitable raw SQL
        raise SQLAlchemyError("Simulated SQL injection test error - invalid SQL executed")
    except Exception:
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


def test_fault_external_api():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}
    mock_api_base_url = os.getenv("MOCK_API_BASE_URL", "http://mock_api:5001").rstrip("/")

    start = time.time()

    try:
        # INTENTIONAL BUG: 3s timeout against mock API with 60% chance of >3s delay
        # and 30% chance of malformed data.
        r = requests.get(f"{mock_api_base_url}/data", timeout=3)
        latency = time.time() - start
        current_app.logger.info("external_call_latency=%.2f", latency)
        r.raise_for_status()
        data = r.json()

        if not isinstance(data, dict) or data.get("value") != 42:
            result = {
                "status": "error",
                "error_code": error_code,
                "detail": "wrong_data",
                "data": data,
                "latency": f"{latency:.2f}s",
            }
            msg = (
                f"{error_code} route=/test-fault/external-api "
                f"reason=wrong_data latency={latency:.2f}"
            )
            print(msg, file=sys.stderr)
            current_app.logger.error(msg)

            try:
                create_live_incident(
                    error_code=error_code,
                    route="/test-fault/external-api",
                    reason="wrong_data",
                    latency=latency,
                )
            except Exception:
                current_app.logger.exception("Failed to create incident for %s", error_code)
        else:
            result = {
                "status": "ok",
                "error_code": None,
                "data": data,
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
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="external_timeout",
                latency=latency,
            )
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
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="upstream_failure",
                latency=latency,
            )
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    except (ValueError, requests.exceptions.JSONDecodeError):
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "wrong_data",
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=wrong_data latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="wrong_data",
                latency=latency,
            )
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
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="connection_error",
                latency=latency,
            )
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (504 if result["status"] == "error" else 200)


def test_fault_db_timeout():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_DB_TIMEOUT"
    result = {"status": "ok", "error_code": None}

    start = time.time()

    try:
        # INTENTIONAL BUG: ~5.5s statement timeout against a longer pg_sleep.
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
        current_app.logger.error(msg)

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