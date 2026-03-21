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


@page.post("/test-fault/external-api")
def test_fault_external_api():
    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}

    start = time.time()

    try:
        # BUG: Timeout set to 0.001s - guarantees timeout on any external call
        mock_api_base = os.environ.get("MOCK_API_BASE_URL", "http://mock_api:5001")
        r = requests.get(f"{mock_api_base}/data", timeout=0.001)
        latency = time.time() - start

        current_app.logger.info(f"external_call_latency={latency:.2f}")

        r.raise_for_status()
        result = {
            "status": "ok",
            "error_code": None,
            "data": r.json(),
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
            create_live_incident(error_code=error_code, route="/test-fault/external-api", reason="external_timeout", latency=latency)
        except Exception:
            current_app.logger.exception("Failed to create live incident")

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
            create_live_incident(error_code=error_code, route="/test-fault/external-api", reason="upstream_failure", latency=latency)
        except Exception:
            current_app.logger.exception("Failed to create live incident")

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
            create_live_incident(error_code=error_code, route="/test-fault/external-api", reason="connection_error", latency=latency)
        except Exception:
            current_app.logger.exception("Failed to create live incident")

    except Exception as e:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": str(e),
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=unhandled_exception latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)
        try:
            create_live_incident(error_code=error_code, route="/test-fault/external-api", reason="unhandled_exception", latency=latency)
        except Exception:
            current_app.logger.exception("Failed to create live incident")

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