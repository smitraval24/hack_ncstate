"""This file handles the views logic for the page part of the project."""

import os
import sys
from importlib.metadata import version

from flask import Blueprint, render_template

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


def _is_parameter_not_found_error(exc: Exception) -> bool:
    """Check if the exception is an SSM ParameterNotFound error."""
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    return error.get("Code") == "ParameterNotFound"


def _is_access_denied_error(exc: Exception) -> bool:
    """Check if the exception is an SSM AccessDenied error."""
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    return error.get("Code") == "AccessDeniedException"


def _safe_clear_fault_cooldown(fault_code: str) -> None:
    """Safely attempt to clear SSM cooldown parameter with proper error handling."""
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        import boto3
        
        ssm = boto3.client("ssm")
        param_name = f"/cream/fault-cooldown/{fault_code}"
        ssm.delete_parameter(Name=param_name)
        logger.info("Successfully cleared cooldown for %s", fault_code)
        
    except Exception as exc:
        if _is_parameter_not_found_error(exc):
            # Parameter doesn't exist, which is fine - nothing to clear
            logger.debug("No cooldown parameter found for %s", fault_code)
        elif _is_access_denied_error(exc):
            # Log as warning but don't raise - this is an infrastructure issue
            logger.warning(
                "Could not clear cooldown for %s due to insufficient permissions. "
                "This is an infrastructure configuration issue that requires "
                "ssm:DeleteParameter permission to be added to the IAM role.",
                fault_code
            )
        else:
            # Other errors should be logged but not raise to avoid breaking the app
            logger.warning("Could not clear cooldown for %s: %s", fault_code, exc)


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

for _mod_name in ("views_sql", "views_db", "views_api"):
    try:
        __import__(f"hello.page.{_mod_name}")
    except Exception:
        _fault_log.exception("Failed to import %s — route disabled", _mod_name)