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
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    )


@page.get("/test-fault")
def test_fault():
    return _render_fault()


@page.post("/test-fault/run")
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


def _make_external_api_call_with_retry(url, max_retries=3, base_timeout=10):
    """
    Make external API call with exponential backoff retry logic.
    Implements circuit breaker pattern to handle connection errors gracefully.
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            # Exponential backoff: 10s, 20s, 40s timeouts
            timeout = base_timeout * (2 ** attempt)
            current_app.logger.info(f"API call attempt {attempt + 1}/{max_retries}, timeout={timeout}s")
            
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response, None
            
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_exception = e
            current_app.logger.warning(f"API call attempt {attempt + 1} failed: {type(e).__name__}")
            
            # Don't sleep after the last attempt
            if attempt < max_retries - 1:
                sleep_time = 2 ** attempt  # 1s, 2s, 4s
                current_app.logger.info(f"Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
                
        except requests.exceptions.HTTPError as e:
            # Don't retry HTTP errors (4xx, 5xx)
            return None, e
    
    return None, last_exception


@page.post("/test-fault/external-api")
def test_fault_external_api():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}

    start = time.time()

    try:
        # Fixed: Use retry mechanism with circuit breaker pattern
        # Increased base timeout to handle API delays (2-8s)
        response, exception = _make_external_api_call_with_retry(
            "http://mock_api:5001/data", 
            max_retries=3, 
            base_timeout=10
        )
        
        latency = time.time() - start
        current_app.logger.info(f"external_call_latency={latency:.2f}")
        
        if response:
            result = {
                "status": "ok",
                "error_code": None,
                "data": response.json(),
                "latency": f"{latency:.2f}s",
            }
        else:
            # Handle the exception that caused all retries to fail
            if isinstance(exception, requests.exceptions.Timeout):
                result = {
                    "status": "error",
                    "error_code": error_code,
                    "detail": "timeout_after_retries",
                    "latency": f"{latency:.2f}s",
                }
                msg = (
                    f"{error_code} route=/test-fault/external-api "
                    f"reason=external_timeout_after_retries latency={latency:.2f}"
                )
                print(msg, file=sys.stderr)
                current_app.logger.error(msg)

                try:
                    create_live_incident(
                        error_code=error_code,
                        route="/test-fault/external-api",
                        reason="external_timeout_after_retries",
                        latency=latency,
                    )
                except Exception:
                    current_app.logger.exception("Failed to create incident for %s", error_code)
                    
            elif isinstance(exception, requests.exceptions.HTTPError):
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
                    
            elif isinstance(exception, requests.exceptions.ConnectionError):
                result = {
                    "status": "error",
                    "error_code": error_code,
                    "detail": "connection_refused_after_retries",
                    "latency": f"{latency:.2f}s",
                }
                msg = (
                    f"{error_code} route=/test-fault/external-api "
                    f"reason=connection_error_after_retries latency={latency:.2f}"
                )
                print(msg, file=sys.stderr)
                current_app.logger.error(msg)

                try:
                    create_live_incident(
                        error_code=error_code,
                        route="/test-fault/external-api",
                        reason="connection_error_after_retries",
                        latency=latency,
                    )
                except Exception:
                    current_app.logger.exception("Failed to create incident for %s", error_code)

    except Exception as e:
        # Catch any unexpected exceptions
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": f"unexpected_error: {str(e)[:100]}",
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=unexpected_error latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="unexpected_error",
                latency=latency,
            )
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (504 if result["status"] == "error" else 200)


@page.post("/test-fault/db-timeout")
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