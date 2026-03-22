"""This file handles the views logic for the page part of the project."""

import os
import sys
import time
import logging
from datetime import datetime
from importlib.metadata import version
from urllib.parse import urlparse, urljoin

import requests
from flask import Blueprint, render_template, current_app, abort
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, TimeoutError

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


def _resolve_live_incidents(error_code: str, route: str, latency: float | None = None) -> list[str]:
    """Mark matching live incidents resolved once a fault path starts succeeding again."""
    now = datetime.now()
    updated: list[str] = []

    try:
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


def _validate_and_sanitize_url(base_url: str) -> str:
    """Validate and sanitize base URL to prevent URL injection attacks."""
    if not base_url:
        raise ValueError("Base URL cannot be empty")

    # Parse the URL to validate its components
    parsed = urlparse(base_url)

    # Only allow http and https protocols
    if parsed.scheme not in ('http', 'https'):
        raise ValueError("Only HTTP and HTTPS protocols are allowed")

    # Ensure hostname is present and valid
    if not parsed.netloc:
        raise ValueError("Invalid hostname in URL")

    # Prevent localhost/private IP access in production (security measure)
    hostname = parsed.hostname
    if hostname:
        hostname_lower = hostname.lower()
        # Block obviously dangerous hostnames
        blocked_hostnames = ['127.0.0.1', 'localhost', '0.0.0.0', '::1']
        if hostname_lower in blocked_hostnames and not DEBUG:
            raise ValueError("Access to localhost/loopback addresses not allowed in production")

    # Reconstruct clean URL (removes any malicious components)
    clean_url = f"{parsed.scheme}://{parsed.netloc}"
    if parsed.path:
        clean_url += parsed.path.rstrip('/')

    return clean_url


def _sanitize_error_message(error: Exception) -> str:
    """Sanitize error message to prevent information disclosure and injection attacks."""
    error_msg = str(error)

    # Truncate to prevent excessive logging
    error_msg = error_msg[:100]

    # Remove potentially dangerous characters that could be used for injection
    dangerous_chars = ["'", '"', ";", "--", "/*", "*/", "<", ">", "&", "|"]
    for char in dangerous_chars:
        error_msg = error_msg.replace(char, "")

    # Remove SQL keywords to prevent information disclosure
    sql_keywords = ["SELECT", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "UNION"]
    for keyword in sql_keywords:
        error_msg = error_msg.replace(keyword.upper(), "[SQL_KEYWORD]")
        error_msg = error_msg.replace(keyword.lower(), "[sql_keyword]")

    return error_msg.strip()


def _safe_database_operation(operation_func, timeout_seconds=2):
    """
    Execute a database operation with proper timeout and connection handling.
    Returns (success, result, error_message, latency)
    """
    start_time = time.time()
    connection = None
    transaction = None

    try:
        # Get a fresh connection from the pool
        connection = db.engine.connect()

        # Start a transaction with timeout
        transaction = connection.begin()

        # Set connection-level timeout
        connection.execute(text(f"SET LOCAL statement_timeout = '{timeout_seconds * 1000}ms'"))

        # Execute the operation
        result = operation_func(connection)

        # Commit transaction
        transaction.commit()

        latency = time.time() - start_time
        return True, result, None, latency

    except (OperationalError, TimeoutError) as e:
        latency = time.time() - start_time
        error_msg = str(e).lower()

        if transaction:
            try:
                transaction.rollback()
            except Exception:
                pass

        if "timeout" in error_msg or "canceling statement" in error_msg:
            return False, None, "db_timeout_or_pool_exhaustion", latency
        else:
            return False, None, "db_connection_error", latency

    except Exception as e:
        latency = time.time() - start_time

        if transaction:
            try:
                transaction.rollback()
            except Exception:
                pass

        return False, None, f"db_unexpected_error: {_sanitize_error_message(e)}", latency

    finally:
        # Ensure connection is properly returned to pool
        if connection:
            try:
                connection.close()
            except Exception:
                current_app.logger.warning("Failed to close database connection")


def _make_external_api_call_with_resilience(url: str, timeout: float = 30.0, max_retries: int = 5):
    """
    Make external API call with enhanced timeout and retry logic for better resilience.
    Returns (success, response_data, error_type, latency)
    """
    start_time = time.time()
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            # Enhanced timeout to handle slow network conditions and service startup delays
            # Increased connect timeout specifically for connection establishment
            response = requests.get(
                url, 
                timeout=(10.0, timeout),  # (connect_timeout, read_timeout)
                headers={'Connection': 'close'}  # Prevent connection reuse issues
            )
            latency = time.time() - start_time
            response.raise_for_status()
            
            return True, response.json(), None, latency
            
        except requests.exceptions.Timeout as e:
            last_exception = e
            latency = time.time() - start_time
            current_app.logger.warning(f"External API timeout on attempt {attempt + 1}/{max_retries + 1}, latency so far: {latency:.2f}s")
            if attempt < max_retries:
                # Progressive backoff: 1s, 2s, 4s, 8s, 16s
                sleep_time = 2 ** attempt
                current_app.logger.info(f"Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
                continue
            return False, None, "external_timeout", latency
            
        except requests.exceptions.ConnectionError as e:
            last_exception = e
            latency = time.time() - start_time
            current_app.logger.warning(f"External API connection error on attempt {attempt + 1}/{max_retries + 1}: {str(e)[:100]}")
            if attempt < max_retries:
                # Progressive backoff for connection errors - service might be starting up
                sleep_time = 2 ** attempt
                current_app.logger.info(f"Retrying connection in {sleep_time}s...")
                time.sleep(sleep_time)
                continue
            return False, None, "connection_error", latency
            
        except requests.exceptions.HTTPError as e:
            last_exception = e
            latency = time.time() - start_time
            status_code = e.response.status_code if e.response else None
            current_app.logger.warning(f"External API HTTP error {status_code} on attempt {attempt + 1}/{max_retries + 1}")
            
            # Retry on 5xx errors (server errors) but not on 4xx (client errors)
            if status_code and status_code >= 500 and attempt < max_retries:
                sleep_time = 2 ** attempt
                current_app.logger.info(f"Retrying server error in {sleep_time}s...")
                time.sleep(sleep_time)
                continue
            return False, None, "upstream_failure", latency
            
        except Exception as e:
            last_exception = e
            latency = time.time() - start_time
            current_app.logger.exception(f"Unexpected error in external API call: {e}")
            if attempt < max_retries:
                sleep_time = 2 ** attempt
                current_app.logger.info(f"Retrying unexpected error in {sleep_time}s...")
                time.sleep(sleep_time)
                continue
            return False, None, "unexpected_error", latency
    
    # Should not reach here, but just in case
    latency = time.time() - start_time
    return False, None, "max_retries_exceeded", latency


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


@page.get("/test-fault")
def test_fault():
    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
    )


@page.post("/test-fault/run")
def test_fault_run():
    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}

    if not ENABLE_FAULT_INJECTION:
        return "", 404

    try:
        # INTENTIONAL: malformed SQL — must always fail
        db.session.execute(text("SELECT FROM"))
    except Exception as e:
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
            pass

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (500 if result["status"] == "error" else 200)


@page.post("/test-fault/external-api")
def test_fault_external_api():
    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}

    if not ENABLE_FAULT_INJECTION:
        return "", 404

    # Enhanced resilience with longer timeout and more retries for connection error handling
    # Increased timeout from 10s to 30s to handle slow mock API responses
    # Increased retries from 3 to 5 to handle transient connection issues
    # Added progressive backoff strategy for better handling of service startup delays
    success, response_data, error_type, latency = _make_external_api_call_with_resilience(
        "http://mock_api:5001/data", 
        timeout=30.0,  # Increased from 10s to 30s for read timeout
        max_retries=5  # Increased from 3 to 5 retries for better resilience
    )

    current_app.logger.info(f"external_call_latency={latency:.2f}")

    if success:
        result = {
            "status": "ok",
            "error_code": None,
            "data": response_data,
            "latency": f"{latency:.2f}s",
        }
        
        # Try to resolve any existing incidents for this route
        try:
            resolved_incidents = _resolve_live_incidents(error_code, "/test-fault/external-api", latency)
            if resolved_incidents:
                current_app.logger.info(f"Resolved {len(resolved_incidents)} incidents after successful API call")
        except Exception:
            current_app.logger.exception("Failed to resolve incidents after successful API call")
    else:
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": error_type,
            "latency": f"{latency:.2f}s",
        }
        
        # Map error types to reasons for consistent logging
        reason_mapping = {
            "external_timeout": "external_timeout",
            "connection_error": "connection_error", 
            "upstream_failure": "upstream_failure",
            "unexpected_error": "unexpected_error",
            "max_retries_exceeded": "connection_error"  # Treat as connection error
        }
        
        reason = reason_mapping.get(error_type, "connection_error")
        
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason={reason} latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason=reason,
                latency=latency,
            )
        except Exception:
            pass

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (504 if result["status"] == "error" else 200)


@page.post("/test-fault/db-timeout")
def test_fault_db_timeout():
    error_code = "FAULT_DB_TIMEOUT"
    result = {"status": "ok", "error_code": None}

    if not ENABLE_FAULT_INJECTION:
        return "", 404

    start = time.time()

    try:
        # INTENTIONAL: pg_sleep(5) with no app-level statement_timeout
        # Relies on DB-level or pool-level timeout to trigger the fault
        # Always causes 5+ second delay, often times out
        db.session.execute(text("SELECT pg_sleep(5);"))
        latency = time.time() - start
        result = {
            "status": "ok",
            "error_code": None,
            "latency": f"{latency:.2f}s",
        }
    except Exception as e:
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
            pass

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (500 if result["status"] == "error" else 200)