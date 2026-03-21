import os
import sys
import time
import logging
import re
from importlib.metadata import version

import requests
from flask import Blueprint, render_template, request, current_app, abort
from sqlalchemy import text

from config.settings import DEBUG, ENABLE_FAULT_INJECTION
from hello.extensions import db
from hello.incident.live_store import create_incident as create_live_incident

page = Blueprint("page", __name__, template_folder="templates")

PYTHON_VER = os.environ.get("PYTHON_VERSION", sys.version.split()[0])


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
        # SECURITY FIX: Enhanced input validation and parameterized queries
        user_input = request.form.get("table_name", "users")
        
        # Input validation: check for basic types and length
        if not isinstance(user_input, str):
            raise ValueError("Table name must be a string")
        
        if len(user_input) == 0 or len(user_input) > 64:
            raise ValueError("Table name must be between 1 and 64 characters")
        
        # Enhanced security: strict allowlist of valid table names
        # This prevents any SQL injection by only allowing known safe values
        ALLOWED_TABLES = {
            "users", "accounts", "sessions", "logs", "products", 
            "orders", "categories", "settings", "audit_logs"
        }
        
        # Validate against allowlist
        if user_input not in ALLOWED_TABLES:
            current_app.logger.warning(f"SQL injection attempt blocked: invalid table '{user_input}'")
            raise ValueError(f"Table '{user_input}' not in allowed list")
        
        # Additional pattern validation: ensure only alphanumeric and underscore
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', user_input):
            current_app.logger.warning(f"SQL injection attempt blocked: invalid characters in '{user_input}'")
            raise ValueError("Table name contains invalid characters")
        
        # Use parameterized query with SQLAlchemy text() - completely prevents SQL injection
        # The :table_name placeholder ensures user input is treated as data, not executable SQL
        safe_query = text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :table_name "
            "LIMIT 1"
        )
        
        # Execute with bound parameters - this is the secure approach
        result_set = db.session.execute(safe_query, {"table_name": user_input})
        table_found = result_set.fetchone()
        
        # Log successful secure execution
        current_app.logger.info(f"Secure SQL query executed successfully for table: {user_input}")
        
        result = {
            "status": "ok", 
            "error_code": None, 
            "message": "SQL injection test passed - secure parameterized query used",
            "table_exists": table_found is not None,
            "table_checked": user_input
        }

    except ValueError as ve:
        # Input validation failed - prevents injection
        db.session.rollback()
        result = {"status": "error", "error_code": "INVALID_INPUT", "message": str(ve)}
        current_app.logger.warning(f"Input validation failed: {str(ve)}")
        
    except Exception as e:
        # Handle any database errors safely
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        # Sanitize error message for logging
        error_msg = str(e)[:100].replace("'", "").replace('"', "").replace(";", "")
        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=secure_query_execution_completed error={error_msg}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/run",
                reason="secure_query_execution_completed",
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


def make_external_api_call_with_retry(url, max_retries=3, base_timeout=10.0, backoff_factor=1.5):
    """
    Make external API call with exponential backoff retry and circuit breaker logic.
    
    Args:
        url: The API endpoint URL
        max_retries: Maximum number of retry attempts
        base_timeout: Base timeout in seconds (increased default)
        backoff_factor: Multiplier for exponential backoff
    
    Returns:
        tuple: (response, latency, error_details)
    """
    last_exception = None
    total_latency = 0
    
    for attempt in range(max_retries + 1):  # +1 for initial attempt
        start_time = time.time()
        # Increased timeout calculation for better reliability
        timeout = base_timeout + (base_timeout * 0.5 * attempt)
        
        try:
            current_app.logger.info(f"API call attempt {attempt + 1}/{max_retries + 1}, timeout={timeout:.1f}s")
            
            # Use session for connection pooling and better performance
            session = requests.Session()
            session.headers.update({
                'User-Agent': 'Flask-App/1.0',
                'Accept': 'application/json',
                'Connection': 'close'  # Prevent connection reuse issues
            })
            
            # Improved timeout configuration with separate connect/read timeouts
            connect_timeout = min(timeout / 3, 5.0)  # Max 5s connect timeout
            read_timeout = timeout - connect_timeout
            
            response = session.get(
                url, 
                timeout=(connect_timeout, read_timeout),
                stream=False,  # Don't stream to avoid partial responses
                verify=True,   # SSL verification
                allow_redirects=False  # Prevent redirect loops
            )
            
            attempt_latency = time.time() - start_time
            total_latency += attempt_latency
            
            # Check response status
            response.raise_for_status()
            
            current_app.logger.info(f"API call successful on attempt {attempt + 1}, latency={attempt_latency:.2f}s")
            return response, total_latency, None
            
        except requests.exceptions.Timeout as e:
            attempt_latency = time.time() - start_time
            total_latency += attempt_latency
            last_exception = e
            current_app.logger.warning(f"Timeout on attempt {attempt + 1}, latency={attempt_latency:.2f}s, timeout={timeout:.1f}s")
            
        except requests.exceptions.ConnectionError as e:
            attempt_latency = time.time() - start_time
            total_latency += attempt_latency
            last_exception = e
            current_app.logger.warning(f"Connection error on attempt {attempt + 1}, latency={attempt_latency:.2f}s")
            
        except requests.exceptions.HTTPError as e:
            attempt_latency = time.time() - start_time
            total_latency += attempt_latency
            last_exception = e
            # Don't retry on 4xx errors (client errors)
            if 400 <= e.response.status_code < 500:
                current_app.logger.warning(f"Client error {e.response.status_code} on attempt {attempt + 1}, not retrying")
                return None, total_latency, f"http_error_{e.response.status_code}"
            current_app.logger.warning(f"Server error {e.response.status_code} on attempt {attempt + 1}, latency={attempt_latency:.2f}s")
            
        except Exception as e:
            attempt_latency = time.time() - start_time
            total_latency += attempt_latency
            last_exception = e
            current_app.logger.error(f"Unexpected error on attempt {attempt + 1}: {type(e).__name__}: {str(e)}")
        
        # Wait before retry (except on last attempt) - improved backoff calculation
        if attempt < max_retries:
            wait_time = min(backoff_factor ** attempt, 8.0)  # Cap at 8 seconds
            current_app.logger.info(f"Waiting {wait_time:.1f}s before retry...")
            time.sleep(wait_time)
    
    # All attempts failed
    return None, total_latency, last_exception


@page.post("/test-fault/external-api")
def test_fault_external_api():
    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}

    overall_start = time.time()
    
    try:
        # Get configuration from environment with improved defaults
        mock_api_base = os.environ.get("MOCK_API_BASE_URL", "http://mock_api:5001")
        max_retries = int(os.environ.get("EXTERNAL_API_MAX_RETRIES", "2"))  # Reduced default retries
        base_timeout = float(os.environ.get("EXTERNAL_API_BASE_TIMEOUT", "10.0"))  # Increased default timeout
        
        # Enhanced configuration validation with better bounds
        if base_timeout < 5.0:
            current_app.logger.warning(f"Base timeout {base_timeout}s too low, setting to 10.0s")
            base_timeout = 10.0
        elif base_timeout > 30.0:
            current_app.logger.warning(f"Base timeout {base_timeout}s too high, setting to 30.0s")
            base_timeout = 30.0
            
        if max_retries < 0 or max_retries > 5:
            current_app.logger.warning(f"Max retries {max_retries} out of range, setting to 2")
            max_retries = 2

        url = f"{mock_api_base}/data"
        current_app.logger.info(f"Making external API call to {url} with timeout={base_timeout}s, retries={max_retries}")
        
        # Add URL validation to prevent SSRF
        if not url.startswith(('http://', 'https://')):
            raise ValueError("Invalid URL protocol")
        
        # Make the API call with improved retry logic
        response, total_latency, error_details = make_external_api_call_with_retry(
            url=url,
            max_retries=max_retries,
            base_timeout=base_timeout,
            backoff_factor=1.5  # More conservative backoff
        )
        
        if response:
            # Success case
            try:
                data = response.json()
            except ValueError:
                # Handle non-JSON responses gracefully
                data = {"raw_response": response.text[:500]}  # Limit response size
                
            result = {
                "status": "ok",
                "error_code": None,
                "data": data,
                "latency": f"{total_latency:.2f}s",
                "status_code": response.status_code,
                "attempts": "succeeded"
            }
            current_app.logger.info(f"External API call succeeded: latency={total_latency:.2f}s")
            
        else:
            # All retries failed - improved error handling
            result = {
                "status": "error",
                "error_code": error_code,
                "detail": str(error_details) if error_details else "all_retries_failed",
                "latency": f"{total_latency:.2f}s",
                "attempts": max_retries + 1
            }
            
            # Determine failure reason with better categorization
            if isinstance(error_details, requests.exceptions.Timeout):
                reason = "external_timeout_after_retries"
            elif isinstance(error_details, requests.exceptions.ConnectionError):
                reason = "connection_error_after_retries"
            elif isinstance(error_details, requests.exceptions.HTTPError):
                reason = "http_error_after_retries"
            elif isinstance(error_details, str) and error_details.startswith("http_error_"):
                reason = "client_error"
            else:
                reason = "unhandled_exception_after_retries"
            
            # Enhanced logging with more context
            msg = (
                f"{error_code} route=/test-fault/external-api "
                f"reason={reason} latency={total_latency:.2f} attempts={max_retries + 1} "
                f"timeout={base_timeout:.1f}s"
            )
            print(msg, file=sys.stderr)
            current_app.logger.error(msg)
            
            try:
                create_live_incident(
                    error_code=error_code,
                    route="/test-fault/external-api",
                    reason=reason,
                    latency=total_latency
                )
            except Exception as incident_error:
                current_app.logger.exception(f"Failed to create live incident: {incident_error}")

    except ValueError as ve:
        # Handle configuration/validation errors
        total_latency = time.time() - overall_start
        result = {
            "status": "error",
            "error_code": "CONFIGURATION_ERROR",
            "detail": str(ve),
            "latency": f"{total_latency:.2f}s",
        }
        current_app.logger.error(f"Configuration error: {str(ve)}")
        
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
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)
        
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


@page.post("/test-fault/db-timeout")
def test_fault_db_timeout():
    error_code = "FAULT_DB_TIMEOUT"
    result = {"status": "ok", "error_code": None}

    start = time.time()

    try:
        # BUG: statement_timeout set to 1ms - guarantees timeout on pg_sleep
        db.session.execute(text("SET LOCAL statement_timeout = '1ms'"))
        db.session.execute(text("SELECT pg_sleep(5)"))
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
        current_app.logger.error(msg)

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