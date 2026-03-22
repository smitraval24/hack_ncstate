"""This file handles the views logic for the page part of the project."""

import os
import sys
import time
from importlib.metadata import version

import requests
from flask import Blueprint, render_template, current_app
from sqlalchemy import text

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
        # FIXED: Use a safe, valid SQL query instead of malformed SQL
        # This query safely selects a constant value to test database connectivity
        db.session.execute(text("SELECT 1 as test_value"))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=database_connection_test_failed"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/run",
                reason="database_connection_test_failed",
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

    # Implement retry logic with exponential backoff
    max_retries = 3
    base_timeout = 10  # Increased timeout to handle expected 2-8s delays
    backoff_factor = 2
    
    for attempt in range(max_retries):
        try:
            # Calculate timeout for current attempt: 10s, 20s, 40s
            timeout = base_timeout * (backoff_factor ** attempt)
            current_app.logger.info(f"API call attempt {attempt + 1}/{max_retries} with timeout {timeout}s")
            
            r = requests.get(f"{mock_api_base_url}/data", timeout=timeout)
            latency = time.time() - start
            current_app.logger.info(f"external_call_latency={latency:.2f}")
            r.raise_for_status()
            result = {
                "status": "ok",
                "error_code": None,
                "data": r.json(),
                "latency": f"{latency:.2f}s",
                "attempts": attempt + 1,
            }
            break  # Success, exit retry loop

        except requests.exceptions.Timeout:
            latency = time.time() - start
            if attempt < max_retries - 1:
                # Not the last attempt, wait before retrying
                wait_time = 1 * (backoff_factor ** attempt)  # 1s, 2s, 4s
                current_app.logger.warning(f"Timeout on attempt {attempt + 1}, retrying in {wait_time}s")
                time.sleep(wait_time)
                continue
            else:
                # Last attempt failed
                result = {
                    "status": "error",
                    "error_code": error_code,
                    "detail": "timeout_after_retries",
                    "latency": f"{latency:.2f}s",
                    "attempts": max_retries,
                }
                msg = (
                    f"{error_code} route=/test-fault/external-api "
                    f"reason=external_timeout_final latency={latency:.2f} attempts={max_retries}"
                )
                print(msg, file=sys.stderr)
                current_app.logger.error(msg)

                try:
                    create_live_incident(
                        error_code=error_code,
                        route="/test-fault/external-api",
                        reason="external_timeout_final",
                        latency=latency,
                    )
                except Exception:
                    current_app.logger.exception("Failed to create incident for %s", error_code)

        except requests.exceptions.HTTPError:
            latency = time.time() - start
            if attempt < max_retries - 1:
                # Not the last attempt, wait before retrying
                wait_time = 1 * (backoff_factor ** attempt)
                current_app.logger.warning(f"HTTP error on attempt {attempt + 1}, retrying in {wait_time}s")
                time.sleep(wait_time)
                continue
            else:
                # Last attempt failed
                result = {
                    "status": "error",
                    "error_code": error_code,
                    "detail": "upstream_500_after_retries",
                    "latency": f"{latency:.2f}s",
                    "attempts": max_retries,
                }
                msg = (
                    f"{error_code} route=/test-fault/external-api "
                    f"reason=upstream_failure_final latency={latency:.2f} attempts={max_retries}"
                )
                print(msg, file=sys.stderr)
                current_app.logger.error(msg)

                try:
                    create_live_incident(
                        error_code=error_code,
                        route="/test-fault/external-api",
                        reason="upstream_failure_final",
                        latency=latency,
                    )
                except Exception:
                    current_app.logger.exception("Failed to create incident for %s", error_code)

        except requests.exceptions.ConnectionError:
            latency = time.time() - start
            if attempt < max_retries - 1:
                # Not the last attempt, wait before retrying
                wait_time = 1 * (backoff_factor ** attempt)
                current_app.logger.warning(f"Connection error on attempt {attempt + 1}, retrying in {wait_time}s")
                time.sleep(wait_time)
                continue
            else:
                # Last attempt failed
                result = {
                    "status": "error",
                    "error_code": error_code,
                    "detail": "connection_refused_after_retries",
                    "latency": f"{latency:.2f}s",
                    "attempts": max_retries,
                }
                msg = (
                    f"{error_code} route=/test-fault/external-api "
                    f"reason=connection_error_final latency={latency:.2f} attempts={max_retries}"
                )
                print(msg, file=sys.stderr)
                current_app.logger.error(msg)

                try:
                    create_live_incident(
                        error_code=error_code,
                        route="/test-fault/external-api",
                        reason="connection_error_final",
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
        # INTENTIONAL BUG: pg_sleep(5) with a 2-second statement timeout
        # The timeout is shorter than the sleep, so this always fails
        db.session.execute(text("SET LOCAL statement_timeout = '2s';"))
        db.session.execute(text("SELECT pg_sleep(5);"))
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
            f"reason=db_timeout_or_pool_exhaustion latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(f"db_error={e!s}")

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/db-timeout",
                reason="db_timeout_or_pool_exhaustion",
                latency=latency,
            )
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (500 if result["status"] == "error" else 200)