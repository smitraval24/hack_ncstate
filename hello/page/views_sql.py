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
from hello.page.views import _is_fault_verification_request, _render_fault, page


def _detect_sql_injection(input_string):
    """Enhanced SQL injection detection with comprehensive pattern matching."""
    if not isinstance(input_string, str):
        return False
    
    # Convert to lowercase for case-insensitive matching
    input_lower = input_string.lower()
    
    # Comprehensive SQL injection patterns with word boundaries
    sql_injection_patterns = [
        # Boolean injections
        r'\b(or\s+1\s*=\s*1|and\s+1\s*=\s*1|or\s+\'a\'\s*=\s*\'a\'|and\s+\'a\'\s*=\s*\'a\')\b',
        # SQL keywords that shouldn't appear in user input
        r'\b(union|select|insert|update|delete|drop|create|alter|exec|execute)\b',
        # SQL functions commonly used in injections
        r'\b(char|ascii|substring|waitfor|delay|benchmark|sleep)\b',
        # Hexadecimal values
        r'0x[0-9a-f]+',
        # Logical operators
        r'(\|\||&&)',
        # HTML-encoded characters
        r'(%3c|%3e|%27|%22)',
        # Comment patterns
        r'(--|\#|/\*)',
        # Quote patterns that could be injection attempts
        r'(\'+|\"+|;)',
        # Time-based injection patterns
        r'\b(waitfor\s+delay|pg_sleep|sleep\()\b'
    ]
    
    for pattern in sql_injection_patterns:
        if re.search(pattern, input_lower):
            current_app.logger.warning(f"SQL injection pattern detected: {pattern}")
            return True
    
    return False


def _validate_input(input_value):
    """Comprehensive input validation with whitelist approach."""
    if not input_value:
        return False, "Empty input"
    
    # Convert to string if not already
    str_value = str(input_value).strip()
    
    # Check for SQL injection patterns
    if _detect_sql_injection(str_value):
        return False, "SQL injection pattern detected"
    
    # Whitelist validation: only allow alphanumeric characters, spaces, hyphens, underscores, and dots
    if not re.match(r'^[a-zA-Z0-9\s\-_.]+$', str_value):
        return False, "Invalid characters detected"
    
    # For numeric inputs, ensure it's a valid integer
    if str_value.isdigit():
        int_value = int(str_value)
        # Bounds checking to prevent integer overflow attacks
        if not (1 <= int_value <= 1000000):
            return False, "Integer value out of bounds"
    else:
        return False, "Non-numeric input detected"
    
    return True, "Valid"


@page.post("/test-fault/run")
def test_fault_run():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}
    verification_only = _is_fault_verification_request()

    try:
        # Get test parameter from request
        raw_param = request.form.get('test_param', '1')
        
        # Enhanced input validation
        is_valid, validation_message = _validate_input(raw_param)
        
        if not is_valid:
            current_app.logger.warning(f"Input validation failed: {validation_message} for input: {raw_param}")
            raise ValueError(f"Input validation failed: {validation_message}")
        
        # Convert to integer after validation
        test_param_int = int(raw_param.strip())
        
        # Use parameterized query with SQLAlchemy's text() function and parameter binding
        # This prevents SQL injection by separating SQL code from data
        query = text("SELECT :param AS test_value")
        result_set = db.session.execute(query, {"param": test_param_int})
        
        # Verify query executed successfully
        row = result_set.fetchone()
        if row and row[0] == test_param_int:
            result["message"] = f"Query executed safely with parameter: {test_param_int}"
            
        db.session.commit()  # Commit successful execution
        
    except Exception as e:
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        if not verification_only:
            msg = (
                f"{error_code} route=/test-fault/run "
                f"reason=sql_injection_prevented"
            )
            print(msg, file=sys.stderr)
            current_app.logger.error(msg)

            try:
                create_live_incident(
                    error_code=error_code,
                    route="/test-fault/run",
                    reason="sql_injection_prevented",
                )
            except Exception:
                current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (500 if result["status"] == "error" else 200)