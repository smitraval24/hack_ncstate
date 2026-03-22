"""This file handles the views logic for the page part of the project."""

import os
import sys
import time
import logging
from datetime import datetime
from importlib.metadata import version
import re

import requests
from flask import Blueprint, render_template, current_app, abort, request
from sqlalchemy import text

from config.settings import DEBUG, ENABLE_FAULT_INJECTION
from hello.extensions import db
from hello.incident.live_store import (
    create_incident as create_live_incident,
    get_all_incidents as get_live_incidents,
    update_incident as update_live_incident,
)

# This blueprint groups related routes for this part of the app.
page = Blueprint("page", __name__, template_folder="templates")

PYTHON_VER = os.environ.get("PYTHON_VERSION", sys.version.split()[0])


# This function handles the log fault event work for this file.
def _log_fault_event(message: str) -> None:
    """Emit a single structured fault log line for CloudWatch subscribers."""
    current_app.logger.error(message)


def _sanitize_input(input_value: str) -> str:
    """Sanitize input to prevent SQL injection attacks."""
    if not isinstance(input_value, str):
        return str(input_value)
    
    # Remove potentially dangerous characters
    sanitized = re.sub(r'[;\'"\\`]', '', input_value)
    # Limit length to prevent buffer overflow
    sanitized = sanitized[:100]
    return sanitized


def _validate_sql_query(query: str) -> bool:
    """Validate SQL query to ensure it's safe for execution."""
    # Only allow specific safe queries for testing
    safe_queries = [
        "SELECT 1 AS test_column",
        "SELECT COUNT(*) FROM information_schema.tables",
        "SELECT current_timestamp"
    ]
    
    normalized_query = ' '.join(query.strip().lower().split())
    safe_normalized = [' '.join(q.strip().lower().split()) for q in safe_queries]
    
    return normalized_query in safe_normalized


def _resolve_live_incidents(error_code: str, route: str, latency: float | None = None) -> list[str]:
    """Mark matching live incidents resolved once a fault path starts succeeding again."""
    now = datetime.now()
    updated: list[str] = []

    try:
        # Sanitize inputs to prevent injection
        error_code = _sanitize_input(error_code)
        route = _sanitize_input(route)
        
        for inc in get_live_incidents():
            if inc.get("error_code") != error_code or inc.get("route") != route:
                continue
            if inc.get("status") == "resolved":
                continue

            result = update_live_incident(
                inc["id"],
                {
                    "status": "resolved",
                    "timestamp_resolved": now,
                    "verification": {
                        "error_rate_before": inc.get("symptoms", {}).get("error_rate_value", 100),
                        "error_rate_after": 0,
                        "latency_before": inc.get("symptoms", {}).get("latency_p95_value", 0),
                        "latency_after": latency or 0,
                        "health_check_status": "passed",
                        "success": True,
                    },
                },
            )
            if result:
                updated.append(inc["id"])
    except Exception:
        current_app.logger.exception("Failed to resolve live incidents for %s", error_code)

    return updated


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


# This function runs the fault work used in this file.
@page.get("/test-fault")
def test_fault():
    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
    )


# This function runs the fault run work used in this file.
@page.post("/test-fault/run")
def test_fault_run():
    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}

    # Check if fault injection is enabled before proceeding
    if not ENABLE_FAULT_INJECTION:
        result = {"status": "disabled", "error_code": None}
        current_app.logger.info("SQL injection test skipped - fault injection disabled")
        return render_template(
            "page/test_fault.html",
            flask_ver=version("flask"),
            python_ver=PYTHON_VER,
            debug=DEBUG,
            enable_fault_injection=ENABLE_FAULT_INJECTION,
            result=result,
        ), 200

    try:
        # Get the test query parameter if provided, default to safe query
        test_query = request.form.get("test_query", "SELECT 1 AS test_column")
        
        # Validate the query to prevent SQL injection
        if not _validate_sql_query(test_query):
            raise ValueError("Invalid or potentially unsafe SQL query detected")
        
        # Use parameterized query for safety
        safe_query = text("SELECT 1 AS test_column")
        db.session.execute(safe_query)
        
        _resolve_live_incidents(error_code, "/test-fault/run")
    except Exception as e:
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}
        error_msg = _sanitize_input(str(e)[:100])
        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=invalid_sql_executed error={error_msg}"
        )
        _log_fault_event(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/run",
                reason="invalid_sql_executed",
            )
        except Exception as incident_error:
            current_app.logger.exception(f"Failed to create live incident: {incident_error}")

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (500 if result["status"] == "error" and result.get("error_code") == error_code else 200)


# This function runs the fault external api work used in this file.
@page.post("/test-fault/external-api")
def test_fault_external_api():
    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}

    overall_start = time.time()

    try:
        mock_api_base = os.environ.get("MOCK_API_BASE_URL", "http://mock_api:5001")
        url = f"{mock_api_base}/data"
        if not url.startswith(('http://', 'https://')):
            raise ValueError("Invalid URL protocol")
        timeout_seconds = float(os.environ.get("EXTERNAL_API_BASE_TIMEOUT", "0.01"))
        timeout_seconds = min(max(timeout_seconds, 0.01), 1.0)

        response = requests.get(url, timeout=timeout_seconds)
        total_latency = time.time() - overall_start
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError:
            data = {"raw_response": response.text[:500]}

        result = {
            "status": "ok",
            "error_code": None,
            "data": data,
            "latency": f"{total_latency:.2f}s",
            "status_code": response.status_code,
        }
        _resolve_live_incidents(error_code, "/test-fault/external-api", total_latency)
    except requests.exceptions.Timeout as e:
        total_latency = time.time() - overall_start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": str(e),
            "latency": f"{total_latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=external_timeout latency={total_latency:.2f}"
        )
        _log_fault_event(msg)
        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="external_timeout",
                latency=total_latency,
            )
        except Exception as incident_error:
            current_app.logger.exception(f"Failed to create live incident: {incident_error}")
    except requests.exceptions.ConnectionError as e:
        total_latency = time.time() - overall_start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": str(e),
            "latency": f"{total_latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=connection_error latency={total_latency:.2f}"
        )
        _log_fault_event(msg)
        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="connection_error",
                latency=total_latency,
            )
        except Exception as incident_error:
            current_app.logger.exception(f"Failed to create live incident: {incident_error}")
    except requests.exceptions.HTTPError as e:
        total_latency = time.time() - overall_start
        reason = "upstream_failure" if e.response is not None and e.response.status_code >= 500 else "client_error"
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": str(e),
            "latency": f"{total_latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason={reason} latency={total_latency:.2f}"
        )
        _log_fault_event(msg)
        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason=reason,
                latency=total_latency,
            )
        except Exception as incident_error:
            current_app.logger.exception(f"Failed to create live incident: {incident_error}")

    except ValueError as ve:
        total_latency = time.time() - overall_start
        result = {
            "status": "error",
            "error_code": "CONFIGURATION_ERROR",
            "detail": str(ve),
            "latency": f"{total_latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=configuration_error latency={total_latency:.2f}"
        )
        _log_fault_event(msg)
        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="configuration_error",
                latency=total_latency
            )
        except Exception as incident_error:
            current_app.logger.exception(f"Failed to create live incident: {incident_error}")
    except Exception as e:
        # Handle unexpected errors in the endpoint itself
        total_latency = time.time() - overall_start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": f"endpoint_error: {str(e)[:200]}",
            "latency": f"{total_latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=endpoint_exception latency={total_latency:.2f}"
        )
        _log_fault_event(msg)
        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="endpoint_exception",
                latency=total_latency
            )
        except Exception as incident_error:
            current_app.logger.exception(f"Failed to create live incident: {incident_error}")

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (504 if result["status"] == "error" and result.get("error_code") == error_code else 200)


# This function runs the fault db timeout work used in this file.
@page.post("/test-fault/db-timeout")
def test_fault_db_timeout():
    error_code = "FAULT_DB_TIMEOUT"
    result = {"status": "ok", "error_code": None}

    start = time.time()

    try:
        db.session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        db.session.execute(text("SELECT pg_sleep(5)"))
        latency = time.time() - start
        result = {
            "status": "ok",
            "error_code": None,
            "latency": f"{latency:.2f}s",
            "message": "Database operation completed successfully"
        }
        _resolve_live_incidents(error_code, "/test-fault/db-timeout", latency)
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
        _log_fault_event(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/db-timeout",
                reason="db_timeout_or_pool_exhaustion",
                latency=latency,
            )
        except Exception:
            current_app.logger.exception("Failed to create live incident")

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (500 if result["status"] == "error" else 200)