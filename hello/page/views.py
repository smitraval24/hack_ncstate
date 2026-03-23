"""This file handles the views logic for the page part of the project."""

import os
import sys
import re
from importlib.metadata import version

from flask import Blueprint, render_template, request

from config.settings import DEBUG, ENABLE_FAULT_INJECTION

# This blueprint groups related routes for this part of the app.
page = Blueprint("page", __name__, template_folder="templates")

PYTHON_VER = os.environ.get("PYTHON_VERSION", sys.version.split()[0])
BUILD_SHA = os.environ.get("BUILD_SHA", "").strip()


def _sanitize_input(input_value):
    """Sanitize input to prevent injection attacks."""
    if not input_value:
        return ""
    
    # Convert to string and strip whitespace
    clean_value = str(input_value).strip()
    
    # Remove potentially dangerous characters
    # Only allow alphanumeric, spaces, hyphens, underscores, and periods
    clean_value = re.sub(r'[^a-zA-Z0-9\s\-_.]', '', clean_value)
    
    # Limit length to prevent buffer overflow
    if len(clean_value) > 255:
        clean_value = clean_value[:255]
    
    return clean_value


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
    """Render fault testing template with proper input sanitization."""
    # Sanitize any result data before rendering
    if result and isinstance(result, dict):
        sanitized_result = {}
        for key, value in result.items():
            if isinstance(value, str):
                sanitized_result[key] = _sanitize_input(value)
            else:
                sanitized_result[key] = value
        result = sanitized_result
    
    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        build_sha=(BUILD_SHA[:7] if BUILD_SHA else "local"),
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    )


def _is_fault_verification_request() -> bool:
    """Return True when a request is probing a fault route after remediation."""
    # Sanitize header and query values to prevent injection
    header_value = _sanitize_input(request.headers.get("X-Fault-Verification", ""))
    query_value = _sanitize_input(request.args.get("verify", ""))
    
    return str(header_value).lower() in {"1", "true", "yes", "on"} or str(
        query_value
    ).lower() in {"1", "true", "yes", "on"}


@page.get("/test-fault")
def test_fault():
    """Render the fault testing page with proper security measures."""
    return _render_fault()


# Import fault route modules so their @page routes get registered.
# Each views_*.py file is the ONLY file the self-healing loop edits
# for its respective fault code.
#
# These imports are wrapped in try/except so that a bad fix pushed by the
# self-healing loop (syntax error, missing import, etc.) cannot crash the
# entire application — only the affected fault route becomes unavailable
# while the rest of the app stays healthy and keeps serving traffic.
import logging as _logging

_fault_log = _logging.getLogger(__name__)

# Enhanced error handling for fault module imports
for _mod_name in ("views_sql", "views_db", "views_api"):
    try:
        __import__(f"hello.page.{_mod_name}")
        _fault_log.info("Successfully imported fault module %s", _mod_name)
    except ImportError as e:
        _fault_log.warning("Fault module %s not found: %s", _mod_name, str(e))
    except SyntaxError as e:
        _fault_log.error("Syntax error in fault module %s: %s", _mod_name, str(e))
    except Exception as e:
        _fault_log.exception("Failed to import fault module %s — route disabled: %s", _mod_name, str(e))


@page.errorhandler(400)
def handle_bad_request(e):
    """Handle bad requests with proper error logging."""
    _fault_log.warning("Bad request received: %s", str(e))
    return render_template("errors/400.html"), 400


@page.errorhandler(500)
def handle_internal_error(e):
    """Handle internal server errors with proper logging."""
    _fault_log.error("Internal server error: %s", str(e))
    return render_template("errors/500.html"), 500