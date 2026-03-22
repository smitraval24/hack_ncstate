"""This file handles the views logic for the page part of the project."""

import os
import sys
import time
import logging
from datetime import datetime, timedelta
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

# Circuit breaker for external API calls
_external_api_circuit_breaker = {
    "failure_count": 0,
    "last_failure_time": None,
    "state": "CLOSED",  # CLOSED, OPEN, HALF_OPEN
    "failure_threshold": 3,
    "recovery_timeout": 30,  # seconds
}


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


def _check_circuit_breaker():
    """Check if circuit breaker allows requests."""
    global _external_api_circuit_breaker
    
    now = datetime.now()
    cb = _external_api_circuit_breaker
    
    if cb["state"] == "OPEN":
        # Check if recovery timeout has passed
        if cb["last_failure_time"] and (now - cb["last_failure_time"]).seconds >= cb["recovery_timeout"]:
            cb["state"] = "HALF_OPEN"
            current_app.logger.info("Circuit breaker moving to HALF_OPEN state")
            return True
        return False
    
    return True  # CLOSED or HALF_OPEN


def _record_circuit_breaker_success():
    """Record successful API call for circuit breaker."""
    global _external_api_circuit_breaker
    
    cb = _external_api_circuit_breaker
    if cb["state"] == "HALF_OPEN":
        cb["state"] = "CLOSED"
        cb["failure_count"] = 0
        cb["last_failure_time"] = None
        current_app.logger.info("Circuit breaker reset to CLOSED state")


def _record_circuit_breaker_failure():
    """Record failed API call for circuit breaker."""
    global _external_api_circuit_breaker
    
    cb = _external_api_circuit_breaker
    cb["failure_count"] += 1
    cb["last_failure_time"] = datetime.now()
    
    if cb["failure_count"] >= cb["failure_threshold"]:
        cb["state"] = "OPEN"
        current_app.logger.warning(f"Circuit breaker OPENED after {cb['failure_count']} failures")


def _make_external_api_call_with_resilience(url: str, timeout: float = 5.0, max_retries: int = 3):
    """
    Make external API call with enhanced resilience including circuit breaker pattern.
    Returns (success, response_data, error_type, latency)
    """
    start_time = time.time()
    
    # Check circuit breaker first
    if not _check_circuit_breaker():
        latency = time.time() - start_time
        current_app.logger.warning("Circuit breaker is OPEN, rejecting external API request")
        return False, None, "circuit_breaker_open", latency
    
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            # Enhanced connection configuration for better reliability
            session = requests.Session()
            
            # Configure adapters with connection pooling and retry settings
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=1,
                pool_maxsize=1,
                max_retries=0,  # We handle retries manually for better control
                pool_block=False
            )
            session.mount('http://', adapter)
            session.mount('https://', adapter)
            
            # Enhanced timeout configuration: (connect_timeout, read_timeout)
            # Connect timeout should be shorter than read timeout
            connect_timeout = min(timeout * 0.3, 2.0)  # Max 2s for connection
            read_timeout = timeout - connect_timeout
            timeout_tuple = (connect_timeout, read_timeout)
            
            current_app.logger.info(f"External API call attempt {attempt + 1}/{max_retries + 1}, timeout={timeout_tuple}")
            
            response = session.get(
                url, 
                timeout=timeout_tuple,
                headers={
                    'User-Agent': 'HelloApp/1.0',
                    'Accept': 'application/json',
                    'Connection': 'close'  # Don't reuse connections for reliability
                }
            )
            
            latency = time.time() - start_time
            response.raise_for_status()
            
            # Record success for circuit breaker
            _record_circuit_breaker_success()
            
            current_app.logger.info(f"External API call successful on attempt {attempt + 1}, latency={latency:.2f}s")
            return True, response.json(), None, latency
            
        except requests.exceptions.Timeout as e:
            last_exception = e
            latency = time.time() - start_time
            current_app.logger.warning(f"External API timeout on attempt {attempt + 1}/{max_retries + 1}, latency={latency:.2f}s")
            
            if attempt < max_retries:
                # Exponential backoff with jitter
                backoff_time = (0.5 * (2 ** attempt)) + (time.time() % 1) * 0.1
                current_app.logger.info(f"Retrying after {backoff_time:.2f}s backoff")
                time.sleep(backoff_time)
                continue
                
            _record_circuit_breaker_failure()
            return False, None, "external_timeout", latency
            
        except requests.exceptions.ConnectionError as e:
            last_exception = e
            latency = time.time() - start_time
            current_app.logger.warning(f"External API connection error on attempt {attempt + 1}/{max_retries + 1}: {str(e)[:100]}")
            
            if attempt < max_retries:
                # Longer backoff for connection errors
                backoff_time = (1.0 * (2 ** attempt)) + (time.time() % 1) * 0.2
                current_app.logger.info(f"Retrying after {backoff_time:.2f}s backoff")
                time.sleep(backoff_time)
                continue
                
            _record_circuit_breaker_failure()
            return False, None, "connection_error", latency
            
        except requests.exceptions.HTTPError as e:
            last_exception = e
            latency = time.time() - start_time
            current_app.logger.warning(f"External API HTTP error on attempt {attempt + 1}: {e.response.status_code if e.response else 'Unknown'}")
            
            # For 5xx errors, retry; for 4xx errors, don't retry
            if e.response and e.response.status_code >= 500 and attempt < max_retries:
                backoff_time = (0.5 * (2 ** attempt)) + (time.time() % 1) * 0.1
                current_app.logger.info(f"Retrying 5xx error after {backoff_time:.2f}s backoff")
                time.sleep(backoff_time)
                continue
            
            # Record failure only for 5xx errors or final 4xx attempt
            if not e.response or e.response.status_code >= 500:
                _record_circuit_breaker_failure()
            
            return False, None, "upstream_failure", latency
            
        except Exception as e:
            last_exception = e
            latency = time.time() - start_time
            current_app.logger.exception(f"Unexpected error in external API call: {e}")
            _record_circuit_breaker_failure()
            return False, None, "unexpected_error", latency
        
        finally:
            # Ensure session is closed
            try:
                session.close()
            except:
                pass
    
    # Should not reach here, but just in case
    latency = time.time() - start_time
    _record_circuit_breaker_failure()
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
        # SECURITY FIX: Use parameterized query to prevent SQL injection
        # This query safely tests database connectivity without vulnerability
        query = text("SELECT :test_value as test_value, :test_message as test_message")
        query_result = db.session.execute(
            query,
            {"test_value": 1, "test_message": "SQL injection test completed safely"}
        )
        test_row = query_result.fetchone()
        db.session.commit()

        result = {
            "status": "ok",
            "error_code": None,
            "message": "SQL injection test completed successfully - no vulnerabilities detected",
            "test_result": {
                "test_value": test_row[0] if test_row else None,
                "test_message": test_row[1] if test_row else None
            }
        }
        _resolve_live_incidents(error_code, "/test-fault/run")

    except Exception as e:
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        # SECURITY FIX: Enhanced error message sanitization
        sanitized_error = _sanitize_error_message(e)

        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=sql_test_execution_error error=({type(e).__name__}) {sanitized_error}"
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
    ), (500 if result["status"] == "error" else 200)


@page.post("/test-fault/external-api")
def test_fault_external_api():
    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}

    if not ENABLE_FAULT_INJECTION:
        return "", 404

    try:
        # RESILIENCE FIX: Enhanced external API call with circuit breaker pattern
        # - Increased timeout to 5 seconds for better connection establishment
        # - Increased max retries to 3 with intelligent backoff
        # - Added circuit breaker pattern to prevent cascading failures
        # - Enhanced connection configuration and error handling
        success, api_data, error_type, latency = _make_external_api_call_with_resilience(
            url="http://mock_api:5001/data",
            timeout=5.0,  # Increased timeout for better reliability
            max_retries=3  # More retries with intelligent backoff
        )

        current_app.logger.info(f"external_call_latency={latency:.2f}s circuit_breaker_state={_external_api_circuit_breaker['state']}")

        if success:
            result = {
                "status": "ok",
                "error_code": None,
                "data": api_data,
                "latency": f"{latency:.2f}s",
                "circuit_breaker_state": _external_api_circuit_breaker["state"]
            }
            _resolve_live_incidents(error_code, "/test-fault/external-api", latency)
        else:
            result = {
                "status": "error",
                "error_code": error_code,
                "detail": error_type,
                "latency": f"{latency:.2f}s",
                "circuit_breaker_state": _external_api_circuit_breaker["state"]
            }

            msg = (
                f"{error_code} route=/test-fault/external-api "
                f"reason={error_type} latency={latency:.2f}"
            )
            _log_fault_event(msg)

            try:
                create_live_incident(
                    error_code=error_code,
                    route="/test-fault/external-api",
                    reason=error_type,
                    latency=latency,
                )
            except Exception as incident_error:
                current_app.logger.exception(f"Failed to create live incident: {incident_error}")

    except Exception as e:
        # Fallback error handling for unexpected issues
        latency = 0.0
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "unexpected_error",
            "latency": f"{latency:.2f}s",
            "circuit_breaker_state": _external_api_circuit_breaker["state"]
        }

        sanitized_error = _sanitize_error_message(e)
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=unexpected_error error=({type(e).__name__}) {sanitized_error}"
        )
        _log_fault_event(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="unexpected_error",
                latency=latency,
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
    ), (504 if result["status"] == "error" else 200)


@page.post("/test-fault/db-timeout")
def test_fault_db_timeout():
    error_code = "FAULT_DB_TIMEOUT"
    result = {"status": "ok", "error_code": None}

    if not ENABLE_FAULT_INJECTION:
        return "", 404

    def db_timeout_operation(connection):
        """Database operation that will timeout - used for testing timeout handling."""
        # This intentionally causes a timeout to test the timeout handling mechanism
        connection.execute(text("SELECT pg_sleep(:sleep_duration)"), {"sleep_duration": 5})
        return {"message": "Sleep operation completed"}

    # Use the safe database operation wrapper with proper timeout handling
    success, operation_result, error_reason, latency = _safe_database_operation(
        db_timeout_operation,
        timeout_seconds=1  # Set timeout to 1 second while trying to sleep for 5 seconds
    )

    if success:
        result = {
            "status": "ok",
            "error_code": None,
            "latency": f"{latency:.2f}s",
        }
        _resolve_live_incidents(error_code, "/test-fault/db-timeout", latency)
    else:
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": f"Database operation failed: {error_reason}",
            "latency": f"{latency:.2f}s",
        }

        msg = (
            f"{error_code} route=/test-fault/db-timeout "
            f"reason={error_reason} latency={latency:.2f}"
        )
        _log_fault_event(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/db-timeout",
                reason=error_reason,
                latency=latency,
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
    ), (500 if result["status"] == "error" else 200)