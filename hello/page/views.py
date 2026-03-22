"""This file handles the views logic for the page part of the project."""

import os
import sys
import time
from importlib.metadata import version

import requests
from flask import Blueprint, render_template, current_app, request
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, TimeoutError

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
        # SECURITY FIX: Use parameterized query to prevent SQL injection
        # This query safely selects a constant value to test database connectivity
        # Using bound parameters prevents any potential SQL injection attacks
        query = text("SELECT :test_value as test_result")
        db.session.execute(query, {"test_value": 1})
        db.session.commit()
        
        # Log successful database test
        current_app.logger.info("Database connectivity test passed successfully")
        
    except Exception as e:
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=database_connection_test_failed error={str(e)[:100]}"
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

    # Implement retry logic with exponential backoff for database operations
    max_retries = 3
    base_delay = 0.5  # Start with 500ms delay
    backoff_factor = 2

    for attempt in range(max_retries):
        try:
            # Set shorter statement timeout to fail fast and allow retries
            timeout_query = text("SET LOCAL statement_timeout = :timeout_value")
            db.session.execute(timeout_query, {"timeout_value": "5s"})
            
            # Use lightweight query for health check - just test basic connectivity
            # This is much more efficient than querying pg_stat_activity
            health_query = text("SELECT 1 as health_check")
            result_set = db.session.execute(health_query)
            health_result = result_set.scalar()
            
            # Commit the transaction properly
            db.session.commit()
            
            latency = time.time() - start
            result = {
                "status": "ok",
                "error_code": None,
                "latency": f"{latency:.2f}s",
                "detail": "database_connection_healthy",
                "attempts": attempt + 1
            }
            
            current_app.logger.info(f"Database health check completed successfully in {latency:.2f}s after {attempt + 1} attempts")
            break  # Success, exit retry loop
            
        except (OperationalError, TimeoutError) as e:
            db.session.rollback()
            latency = time.time() - start
            
            if attempt < max_retries - 1:
                # Not the last attempt, wait before retrying with exponential backoff
                delay = base_delay * (backoff_factor ** attempt)  # 0.5s, 1s, 2s
                current_app.logger.warning(f"Database timeout on attempt {attempt + 1}/{max_retries}, retrying in {delay}s")
                time.sleep(delay)
                continue
            else:
                # Last attempt failed
                result = {
                    "status": "error",
                    "error_code": error_code,
                    "detail": f"db_timeout_after_retries: {str(e)[:200]}",
                    "latency": f"{latency:.2f}s",
                    "attempts": max_retries,
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
                    
        except Exception as e:
            # Handle other database errors
            db.session.rollback()
            latency = time.time() - start
            
            if attempt < max_retries - 1:
                # Not the last attempt, wait before retrying
                delay = base_delay * (backoff_factor ** attempt)
                current_app.logger.warning(f"Database error on attempt {attempt + 1}/{max_retries}, retrying in {delay}s: {str(e)[:100]}")
                time.sleep(delay)
                continue
            else:
                # Last attempt failed
                result = {
                    "status": "error",
                    "error_code": error_code,
                    "detail": f"unexpected_db_error_after_retries: {str(e)[:200]}",
                    "latency": f"{latency:.2f}s",
                    "attempts": max_retries,
                }
                current_app.logger.error(f"Unexpected database error after {max_retries} attempts: {e!s}")

    return _render_fault(result), (500 if result["status"] == "error" else 200)