"""This file handles the views logic for the page part of the project."""

import os
import sys
from importlib.metadata import version

from flask import Blueprint, render_template, request

from config.settings import DEBUG, ENABLE_FAULT_INJECTION

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
        
        result = f"Query processed safely: {sanitized_input[:100]}"  # Limit output length
    else:
        result = "No query provided"
    
    return _render_fault(result)