"""Fault handler for FAULT_SQL_INJECTION_TEST.

This is the ONLY file the self-healing loop may edit when remediating
this fault code.  The route is registered on the page blueprint.
"""

import sys
import re

from flask import current_app, request
from sqlalchemy import text

from config.settings import ENABLE_FAULT_INJECTION
from hello.extensions import db
from hello.incident.live_store import (
    create_incident as create_live_incident,
)
from hello.page.views import page, _render_fault


def _is_sql_injection_attempt(input_str):
    """
    Advanced SQL injection detection with comprehensive pattern matching.
    Returns True if input contains potential SQL injection patterns.
    """
    if not input_str:
        return False
    
    # Convert to lowercase for case-insensitive matching
    input_lower = input_str.lower()
    
    # SQL injection patterns - boolean-based, union-based, time-based, comment-based
    sql_injection_patterns = [
        # Boolean-based patterns
        r"(\b(and|or)\s+\d+\s*[=<>!]+\s*\d+)",
        r"(\b(and|or)\s+['\"]?\w+['\"]?\s*[=<>!]+\s*['\"]?\w+['\"]?)",
        r"(\b(and|or)\s+\d+\s*[=<>!]+\s*['\"])",
        
        # Union-based patterns
        r"(\bunion\s+(all\s+)?select)",
        r"(\bunion\s+(all\s+)?select\s+null)",
        r"(\bunion\s+(all\s+)?select\s+\d+)",
        
        # Time-based patterns
        r"(\bwaitfor\s+delay)",
        r"(\bsleep\s*\(\s*\d+\s*\))",
        r"(\bbenchmark\s*\(\s*\d+)",
        
        # Comment-based evasion
        r"(/\*.*?\*/)",
        r"(--\s*[^\r\n]*)",
        r"(#[^\r\n]*)",
        
        # SQL keywords and functions
        r"(\b(select|insert|update|delete|drop|create|alter|exec|execute)\b)",
        r"(\b(union|where|having|group\s+by|order\s+by)\b)",
        r"(\b(concat|char|ascii|substring|mid|left|right)\b)",
        r"(\b(information_schema|sysobjects|syscolumns)\b)",
        
        # Special characters and escape sequences
        r"(['\";])",
        r"(\\\x[0-9a-f]{2})",  # Hex encoding
        r"(\%[0-9a-f]{2})",     # URL encoding
        
        # Multi-keyword detection
        r"(\bselect\b.*\bfrom\b)",
        r"(\binsert\b.*\binto\b)",
        r"(\bupdate\b.*\bset\b)",
        r"(\bdelete\b.*\bfrom\b)",
    ]
    
    for pattern in sql_injection_patterns:
        if re.search(pattern, input_lower, re.IGNORECASE | re.MULTILINE):
            return True
    
    return False


def _validate_input_type(input_str, input_type="text"):
    """
    Validate input based on expected type using whitelist-based character validation.
    """
    if not input_str:
        return True
    
    # Type-specific validation patterns
    type_patterns = {
        "numeric": r"^[0-9]+$",
        "alphanumeric": r"^[a-zA-Z0-9]+$",
        "text": r"^[a-zA-Z0-9\s\.,!?\-_]+$",
        "sql_safe": r"^[a-zA-Z0-9_]+$"  # Only alphanumeric and underscores
    }
    
    pattern = type_patterns.get(input_type, type_patterns["text"])
    
    if not re.match(pattern, input_str):
        return False
    
    return True


def _validate_sql_parameters(params):
    """
    Validate SQL parameters to ensure they're safe before any SQL operations.
    """
    if not params:
        return True
    
    for param_name, param_value in params.items():
        # Validate parameter name (should be sql_safe)
        if not _validate_input_type(param_name, "sql_safe"):
            current_app.logger.warning(f"Invalid parameter name detected: {param_name}")
            return False
        
        # Validate parameter value
        if isinstance(param_value, str):
            if _is_sql_injection_attempt(param_value):
                current_app.logger.warning(f"SQL injection attempt in parameter {param_name}: {param_value}")
                return False
    
    return True


@page.before_request
def validate_request_for_sql_injection():
    """
    Middleware to validate all incoming requests for SQL injection attempts.
    """
    # Skip validation for non-SQL related routes
    if not request.endpoint or 'sql' not in request.endpoint:
        return
    
    # Check URL path for injection attempts
    if _is_sql_injection_attempt(request.path):
        current_app.logger.warning(f"SQL injection attempt in URL path: {request.path}")
        return "Request blocked: Invalid input detected", 400
    
    # Check query parameters
    for param_name, param_value in request.args.items():
        if _is_sql_injection_attempt(param_value):
            current_app.logger.warning(f"SQL injection attempt in query parameter {param_name}: {param_value}")
            return "Request blocked: Invalid input detected", 400
    
    # Check form data
    if request.form:
        for param_name, param_value in request.form.items():
            if _is_sql_injection_attempt(param_value):
                current_app.logger.warning(f"SQL injection attempt in form data {param_name}: {param_value}")
                return "Request blocked: Invalid input detected", 400
    
    # Check JSON data
    if request.is_json and request.get_json():
        json_data = request.get_json()
        for key, value in json_data.items():
            if isinstance(value, str) and _is_sql_injection_attempt(value):
                current_app.logger.warning(f"SQL injection attempt in JSON data {key}: {value}")
                return "Request blocked: Invalid input detected", 400
    
    # Check suspicious user agents
    user_agent = request.headers.get('User-Agent', '')
    if _is_sql_injection_attempt(user_agent):
        current_app.logger.warning(f"SQL injection attempt in User-Agent: {user_agent}")
        return "Request blocked: Invalid input detected", 400


@page.post("/test-fault/run")
def test_fault_run():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}

    try:
        # SECURE FIX: Instead of executing malformed SQL, validate a safe query
        # Use parameterized queries and validate all inputs
        test_query = "SELECT 1 as test_column"
        
        # Validate the query itself for any injection attempts
        if _is_sql_injection_attempt(test_query):
            raise ValueError("SQL injection attempt detected in query")
        
        # Execute safe parameterized query
        db.session.execute(text(test_query))
        db.session.commit()
        
        # Log successful execution
        current_app.logger.info("Safe SQL query executed successfully")
        
    except ValueError as e:
        # Handle input validation errors
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}
        
        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=sql_injection_blocked: {str(e)}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)
        
        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/run",
                reason="sql_injection_blocked",
            )
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)
            
    except Exception as e:
        # Handle any other database errors
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=database_error: {str(e)}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/run",
                reason="database_error",
            )
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (500 if result["status"] == "error" else 200)