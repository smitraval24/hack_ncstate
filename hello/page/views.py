"""This file handles the views logic for the page part of the project."""

import os
import sys
import time
from importlib.metadata import version

from flask import Blueprint, render_template, request

from config.settings import DEBUG, ENABLE_FAULT_INJECTION

# This blueprint groups related routes for this part of the app.
page = Blueprint("page", __name__, template_folder="templates")

PYTHON_VER = os.environ.get("PYTHON_VERSION", sys.version.split()[0])
BUILD_SHA = os.environ.get("BUILD_SHA", "").strip()

# Database timeout configuration
DB_TIMEOUT_SECONDS = int(os.environ.get("DB_TIMEOUT_SECONDS", "3"))
MAX_RETRY_ATTEMPTS = int(os.environ.get("DB_MAX_RETRIES", "2"))


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


def _execute_db_operation_with_timeout(operation_func, *args, **kwargs):
    """Execute database operation with timeout and retry logic."""
    for attempt in range(MAX_RETRY_ATTEMPTS + 1):
        try:
            start_time = time.time()
            result = operation_func(*args, **kwargs)
            elapsed_time = time.time() - start_time
            
            if elapsed_time > DB_TIMEOUT_SECONDS:
                raise TimeoutError(f"Database operation exceeded timeout of {DB_TIMEOUT_SECONDS}s")
            
            return result
            
        except (TimeoutError, ConnectionError, OSError) as e:
            if attempt < MAX_RETRY_ATTEMPTS:
                time.sleep(0.1 * (attempt + 1))  # Exponential backoff
                continue
            else:
                raise e


@page.get("/test-fault/db-timeout")
def test_db_timeout():
    """Test endpoint for database timeout scenarios."""
    if not ENABLE_FAULT_INJECTION:
        return _render_fault("Fault injection is disabled")
    
    try:
        # Simulate database operation with timeout handling
        def simulate_db_query():
            # Simulate a potentially slow database operation
            time.sleep(0.5)  # Normal operation time
            return "Database query completed successfully"
        
        result = _execute_db_operation_with_timeout(simulate_db_query)
        return _render_fault(f"Success: {result}")
        
    except TimeoutError as e:
        return _render_fault(f"Database timeout error: {str(e)}")
    except Exception as e:
        return _render_fault(f"Database error: {str(e)}")


@page.post("/test-fault/run")
def run_test_fault():
    """Handle test fault execution with proper input validation."""
    if not ENABLE_FAULT_INJECTION:
        return _render_fault("Fault injection is disabled")
    
    # Get user input safely
    user_input = request.form.get('query', '')
    
    # Validate and sanitize input to prevent SQL injection
    if user_input:
        # Remove potentially dangerous SQL keywords and characters
        dangerous_patterns = [
            ';', '--', '/*', '*/', 'DROP', 'DELETE', 'INSERT', 'UPDATE', 
            'EXEC', 'EXECUTE', 'UNION', 'SELECT', 'CREATE', 'ALTER'
        ]
        
        sanitized_input = user_input
        for pattern in dangerous_patterns:
            sanitized_input = sanitized_input.replace(pattern.upper(), '')
            sanitized_input = sanitized_input.replace(pattern.lower(), '')
        
        # Only allow alphanumeric characters, spaces, and safe punctuation
        import re
        sanitized_input = re.sub(r'[^a-zA-Z0-9\s\.\,\!\?]', '', sanitized_input)
        
        try:
            # Execute query with timeout protection
            def process_query():
                return f"Query processed safely: {sanitized_input[:100]}"
            
            result = _execute_db_operation_with_timeout(process_query)
            
        except TimeoutError as e:
            result = f"Query timeout error: {str(e)}"
        except Exception as e:
            result = f"Query processing error: {str(e)}"
    else:
        result = "No query provided"
    
    return _render_fault(result)