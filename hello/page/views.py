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


def _safe_clear_fault_cooldown(fault_code: str) -> bool:
    """Safely attempt to clear SSM cooldown parameter with proper error handling.
    
    Returns:
        bool: True if parameter was successfully cleared or didn't exist, False if access denied
    """
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        import boto3
        from botocore.exceptions import ClientError, BotoCoreError
        
        ssm = boto3.client("ssm")
        param_name = f"/cream/fault-cooldown/{fault_code}"
        ssm.delete_parameter(Name=param_name)
        logger.info("Successfully cleared cooldown for %s", fault_code)
        return True
        
    except (ClientError, BotoCoreError) as exc:
        if _is_parameter_not_found_error(exc):
            # Parameter doesn't exist, which is fine - nothing to clear
            logger.debug("No cooldown parameter found for %s", fault_code)
            return True
        elif _is_access_denied_error(exc):
            # Log as debug instead of warning to reduce noise - this is expected in restricted environments
            logger.debug(
                "Skipping cooldown clear for %s: insufficient SSM permissions. "
                "This is expected in restricted environments.",
                fault_code
            )
            return False
        else:
            # Other AWS errors should be logged as debug to avoid noise in restricted environments
            logger.debug("Could not clear cooldown for %s: %s", fault_code, exc)
            return False
    except Exception as exc:
        # Non-AWS errors (import errors, etc.) should be logged as debug to reduce noise
        logger.debug("Unexpected error clearing cooldown for %s: %s", fault_code, exc)
        return False


def clear_fault_cooldown(fault_code: str) -> bool:
    """Public interface for clearing fault cooldowns with proper error handling.
    
    This function should be used by all modules that need to clear fault cooldowns
    to ensure consistent error handling and prevent AccessDeniedException from
    breaking the application flow.
    
    Args:
        fault_code: The fault code to clear cooldown for (e.g., 'FAULT_DB_TIMEOUT')
        
    Returns:
        bool: True if successfully cleared or no action needed, False if access denied
    """
    return _safe_clear_fault_cooldown(fault_code)


def safe_ssm_operation(operation_type: str, param_name: str, param_value: str = None) -> tuple[bool, str]:
    """Safely perform SSM operations with proper error handling and reduced log noise.
    
    Args:
        operation_type: 'get', 'put', or 'delete'
        param_name: SSM parameter name
        param_value: Value for put operations (optional)
        
    Returns:
        tuple: (success: bool, result_or_error: str)
    """
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        import boto3
        from botocore.exceptions import ClientError, BotoCoreError
        
        ssm = boto3.client("ssm")
        
        if operation_type == "get":
            response = ssm.get_parameter(Name=param_name)
            return True, response["Parameter"]["Value"]
        elif operation_type == "put":
            if param_value is None:
                return False, "No value provided for put operation"
            ssm.put_parameter(Name=param_name, Value=param_value, Overwrite=True)
            return True, "Parameter set successfully"
        elif operation_type == "delete":
            ssm.delete_parameter(Name=param_name)
            return True, "Parameter deleted successfully"
        else:
            return False, f"Unknown operation type: {operation_type}"
            
    except (ClientError, BotoCoreError) as exc:
        if _is_parameter_not_found_error(exc):
            if operation_type == "delete":
                logger.debug("Parameter %s not found for deletion (already cleared)", param_name)
                return True, "Parameter already cleared"
            else:
                logger.debug("Parameter %s not found", param_name)
                return False, "Parameter not found"
        elif _is_access_denied_error(exc):
            # Log as debug to reduce noise - this is expected in restricted environments
            logger.debug(
                "Access denied for SSM %s operation on %s. "
                "This is expected in restricted environments.",
                operation_type, param_name
            )
            return False, "Access denied (expected in restricted environments)"
        else:
            # Other AWS errors logged as debug to avoid noise in restricted environments
            logger.debug("SSM %s operation failed for %s: %s", operation_type, param_name, exc)
            return False, str(exc)
    except Exception as exc:
        # Non-AWS errors logged as debug to reduce noise
        logger.debug("Unexpected error in SSM %s operation for %s: %s", operation_type, param_name, exc)
        return False, str(exc)


def get_safe_ssm_client():
    """Get a safe SSM client wrapper that automatically handles permission errors.
    
    This should be used instead of boto3.client('ssm') directly to ensure
    consistent error handling across the application.
    """
    import logging
    
    class SafeSSMClient:
        def __init__(self):
            self.logger = logging.getLogger(__name__)
            try:
                import boto3
                self._client = boto3.client("ssm")
            except Exception as exc:
                self.logger.debug("Failed to create SSM client: %s", exc)
                self._client = None
        
        def delete_parameter(self, Name: str, **kwargs):
            """Safely delete SSM parameter with proper error handling."""
            if not self._client:
                self.logger.debug("SSM client not available")
                return False
                
            try:
                self._client.delete_parameter(Name=Name, **kwargs)
                self.logger.debug("Successfully deleted parameter: %s", Name)
                return True
            except Exception as exc:
                if _is_parameter_not_found_error(exc):
                    self.logger.debug("Parameter %s not found for deletion (already cleared)", Name)
                    return True
                elif _is_access_denied_error(exc):
                    self.logger.debug(
                        "Access denied for delete operation on %s. "
                        "This is expected in restricted environments.", Name
                    )
                    return False
                else:
                    self.logger.debug("Failed to delete parameter %s: %s", Name, exc)
                    return False
        
        def get_parameter(self, Name: str, **kwargs):
            """Safely get SSM parameter with proper error handling."""
            if not self._client:
                raise Exception("SSM client not available")
            return self._client.get_parameter(Name=Name, **kwargs)
        
        def put_parameter(self, Name: str, Value: str, **kwargs):
            """Safely put SSM parameter with proper error handling."""
            if not self._client:
                raise Exception("SSM client not available")
            return self._client.put_parameter(Name=Name, Value=Value, **kwargs)
    
    return SafeSSMClient()


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


# Register the stable fault routes after the blueprint and render helpers exist.
from hello.page import _fault_cores as _fault_routes  # noqa: F401
