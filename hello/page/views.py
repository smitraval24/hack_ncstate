"""This file handles the views logic for the page part of the project."""

import os
import sys
import re
import html
from importlib.metadata import version

from flask import Blueprint, render_template, request

from config.settings import DEBUG, ENABLE_FAULT_INJECTION

# This blueprint groups related routes for this part of the app.
page = Blueprint("page", __name__, template_folder="templates")

PYTHON_VER = os.environ.get("PYTHON_VERSION", sys.version.split()[0])
BUILD_SHA = os.environ.get("BUILD_SHA", "").strip()

# Enhanced SQL injection prevention patterns with additional coverage
SQL_INJECTION_PATTERNS = [
    r"('|(\\x27)|(\\x2D)|(\\x2D)|(\\x23)|(\\x3B)|(\\x3D))",  # SQL chars
    r"((\\%3D)|(\\%27)|(\\%3B)|(\\%23)|(\\%2D)|(\\%3C)|(\\%3E))",  # URL encoded
    r"(union|select|insert|update|delete|drop|create|alter|exec|execute|script|onload|onerror)",  # SQL keywords
    r"(javascript:|vbscript:|data:|file:|ftp:)",  # Script injection
    r"(<script|<iframe|<object|<embed|<link|<style)",  # HTML injection
    r"(;|\||&|\$|`|<|>|\*|\?|\[|\]|\{|\}|\(|\))",  # Command injection chars
    r"(\bor\b|\band\b).*(\b1\b|\btrue\b).*(\=|\bis\b)",  # Boolean-based SQL injection
    r"(\bunion\b.*\bselect\b)|(\bselect\b.*\bfrom\b)",  # Union and select combinations
    r"(\'.*\bor\b.*\'|\\".*\bor\b.*\\")",  # Quoted OR conditions
    r"(\bwaitfor\b|\bdelay\b|\bbenchmark\b|\bsleep\b)",  # Time-based injection
]

# Whitelist of allowed characters for different input types
SAFE_ALPHANUMERIC = re.compile(r'^[a-zA-Z0-9_-]+$')
SAFE_TEXT = re.compile(r'^[a-zA-Z0-9\s.,!?\'"()-]+$')
SAFE_NUMERIC = re.compile(r'^[0-9.-]+$')

def _is_sql_injection_attempt(input_value):
    """Comprehensive SQL injection detection."""
    if not input_value:
        return False
    
    input_str = str(input_value).lower()
    
    # Check against all injection patterns
    for pattern in SQL_INJECTION_PATTERNS:
        if re.search(pattern, input_str, re.IGNORECASE):
            return True
    
    # Check for multiple SQL keywords in sequence (common in injections)
    sql_keywords = ['select', 'union', 'insert', 'update', 'delete', 'drop', 'create', 'alter']
    keyword_count = sum(1 for keyword in sql_keywords if keyword in input_str)
    if keyword_count >= 2:
        return True
    
    # Check for comment patterns used in SQL injection
    if '--' in input_str or '/*' in input_str or '*/' in input_str:
        return True
    
    # Check for hex encoding attempts
    if '0x' in input_str and any(c in input_str for c in 'abcdef'):
        return True
    
    return False

def _sanitize_input(input_value, input_type='text'):
    """Enhanced sanitization to prevent SQL injection and XSS attacks."""
    if not input_value:
        return ""
    
    # Convert to string and strip whitespace
    clean_value = str(input_value).strip()
    
    # Immediate rejection for SQL injection attempts
    if _is_sql_injection_attempt(clean_value):
        _fault_log.error("SQL injection attempt blocked: %s", clean_value[:50])
        raise ValueError("Invalid input detected")
    
    # Apply input type-specific validation
    if input_type == 'numeric':
        if not SAFE_NUMERIC.match(clean_value):
            _fault_log.warning("Invalid numeric input blocked: %s", clean_value[:50])
            raise ValueError("Invalid numeric input")
    elif input_type == 'alphanumeric':
        if not SAFE_ALPHANUMERIC.match(clean_value):
            _fault_log.warning("Invalid alphanumeric input blocked: %s", clean_value[:50])
            raise ValueError("Invalid alphanumeric input")
    
    # HTML escape to prevent XSS
    clean_value = html.escape(clean_value)
    
    # Remove potentially dangerous characters (more restrictive than before)
    if input_type == 'text':
        clean_value = re.sub(r'[^a-zA-Z0-9\s\-_.,!?@#$%()[]{}"\':]', '', clean_value)
    elif input_type == 'sql_safe':
        # Extra restrictive for SQL-related inputs
        clean_value = re.sub(r'[^a-zA-Z0-9_]', '', clean_value)
    
    # Limit length to prevent buffer overflow
    max_length = 100 if input_type == 'sql_safe' else 255
    if len(clean_value) > max_length:
        clean_value = clean_value[:max_length]
    
    return clean_value


def _sanitize_sql_input(input_value):
    """Ultra-strict sanitization for SQL-related inputs with zero tolerance."""
    if not input_value:
        return ""
    
    # Use the enhanced sanitize_input with sql_safe type
    try:
        return _sanitize_input(input_value, 'sql_safe')
    except ValueError:
        # Any validation error results in empty string for SQL inputs
        return ""


def _validate_sql_parameters(params):
    """Validate and sanitize SQL parameters using parameterized queries approach."""
    if not params:
        return {}
    
    validated_params = {}
    for key, value in params.items():
        # Sanitize parameter names (keys)
        safe_key = _sanitize_input(str(key), 'alphanumeric')
        if not safe_key or safe_key != str(key):
            _fault_log.error("Invalid parameter name blocked: %s", str(key)[:50])
            continue
        
        # Validate parameter values
        if isinstance(value, str):
            if _is_sql_injection_attempt(value):
                _fault_log.error("SQL injection in parameter '%s' blocked: %s", safe_key, str(value)[:50])
                continue
            validated_params[safe_key] = _sanitize_sql_input(value)
        elif isinstance(value, (int, float)):
            validated_params[safe_key] = value
        elif isinstance(value, bool):
            validated_params[safe_key] = value
        else:
            # Convert other types to string and sanitize
            str_value = str(value)
            if not _is_sql_injection_attempt(str_value):
                validated_params[safe_key] = _sanitize_sql_input(str_value)
    
    return validated_params


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
    """Render fault testing template with enhanced security measures."""
    # Enhanced sanitization for result data with strict validation
    if result and isinstance(result, dict):
        sanitized_result = {}
        for key, value in result.items():
            try:
                safe_key = _sanitize_input(str(key), 'alphanumeric')
                if not safe_key:
                    continue
                    
                if isinstance(value, str):
                    # Use ultra-strict sanitization for any database-related fields
                    if any(term in key.lower() for term in ['sql', 'query', 'database', 'table', 'column', 'where', 'select']):
                        sanitized_result[safe_key] = _sanitize_sql_input(value)
                    else:
                        sanitized_result[safe_key] = _sanitize_input(value, 'text')
                elif isinstance(value, (list, tuple)):
                    # Sanitize list/tuple elements with strict validation
                    sanitized_list = []
                    for item in value:
                        if isinstance(item, str):
                            if not _is_sql_injection_attempt(item):
                                sanitized_list.append(_sanitize_input(item, 'text'))
                        else:
                            sanitized_list.append(item)
                    sanitized_result[safe_key] = sanitized_list
                elif isinstance(value, (int, float, bool)):
                    sanitized_result[safe_key] = value
                else:
                    # Convert to string and sanitize
                    str_value = str(value)
                    if not _is_sql_injection_attempt(str_value):
                        sanitized_result[safe_key] = _sanitize_input(str_value, 'text')
            except ValueError as e:
                _fault_log.warning("Dropping unsafe result field '%s': %s", str(key)[:50], str(e))
                continue
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
    # Enhanced sanitization for headers and query parameters with validation
    try:
        header_value = _sanitize_input(request.headers.get("X-Fault-Verification", ""), 'alphanumeric')
        query_value = _sanitize_input(request.args.get("verify", ""), 'alphanumeric')
    except ValueError:
        _fault_log.warning("Invalid verification request parameters blocked from IP: %s", 
                          request.environ.get('REMOTE_ADDR', 'unknown'))
        return False
    
    # Additional security check - log verification attempts
    if header_value or query_value:
        _fault_log.info("Fault verification request detected from IP: %s", 
                       request.environ.get('REMOTE_ADDR', 'unknown'))
    
    return str(header_value).lower() in {"1", "true", "yes", "on"} or str(
        query_value
    ).lower() in {"1", "true", "yes", "on"}


@page.get("/test-fault")
def test_fault():
    """Render the fault testing page with comprehensive security measures."""
    # Log access attempts for security monitoring
    _fault_log.info("Fault testing page accessed from IP: %s", 
                   request.environ.get('REMOTE_ADDR', 'unknown'))
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

# Enhanced error handling for fault module imports with security logging
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
    """Handle bad requests with enhanced security logging."""
    _fault_log.warning("Bad request received from IP %s: %s", 
                      request.environ.get('REMOTE_ADDR', 'unknown'), str(e))
    return render_template("errors/400.html"), 400


@page.errorhandler(500)
def handle_internal_error(e):
    """Handle internal server errors with proper logging."""
    _fault_log.error("Internal server error from IP %s: %s", 
                     request.environ.get('REMOTE_ADDR', 'unknown'), str(e))
    return render_template("errors/500.html"), 500


# Enhanced security middleware to prevent SQL injection attacks
@page.before_request
def sanitize_request_data():
    """Sanitize all incoming request data with strict SQL injection prevention."""
    try:
        # Sanitize query parameters with strict validation
        if request.args:
            sanitized_args = {}
            for key, value in request.args.items():
                safe_key = _sanitize_input(key, 'alphanumeric')
                if safe_key and not _is_sql_injection_attempt(value):
                    sanitized_args[safe_key] = _sanitize_input(value, 'text')
            request.sanitized_args = sanitized_args
        
        # Sanitize form data for POST requests with enhanced validation
        if request.method in ['POST', 'PUT', 'PATCH'] and request.form:
            sanitized_form = {}
            for key, value in request.form.items():
                safe_key = _sanitize_input(key, 'alphanumeric')
                if not safe_key or _is_sql_injection_attempt(value):
                    _fault_log.warning("Dropping unsafe form field '%s' from IP: %s", 
                                     str(key)[:50], request.environ.get('REMOTE_ADDR', 'unknown'))
                    continue
                
                # Use ultra-strict sanitization for database-related fields
                if any(term in key.lower() for term in ['sql', 'query', 'database', 'table', 'column']):
                    sanitized_form[safe_key] = _sanitize_sql_input(value)
                else:
                    sanitized_form[safe_key] = _sanitize_input(value, 'text')
            request.sanitized_form = sanitized_form
        
        # Enhanced JSON data sanitization for API requests
        if request.is_json and request.get_json(silent=True):
            try:
                json_data = request.get_json()
                if isinstance(json_data, dict):
                    sanitized_json = _validate_sql_parameters(json_data)
                    request.sanitized_json = sanitized_json
            except Exception as e:
                _fault_log.warning("Failed to sanitize JSON data from IP %s: %s", 
                                 request.environ.get('REMOTE_ADDR', 'unknown'), str(e))
        
    except Exception as e:
        _fault_log.error("Request sanitization failed from IP %s: %s", 
                        request.environ.get('REMOTE_ADDR', 'unknown'), str(e))
        # Continue processing but log the error
    
    # Enhanced suspicious request detection
    user_agent = request.headers.get('User-Agent', '')
    suspicious_patterns = ['sqlmap', 'havij', 'blind', 'injection', 'nmap', 'nikto', 'burp', 'acunetix']
    
    if any(pattern in user_agent.lower() for pattern in suspicious_patterns):
        _fault_log.warning("Suspicious user agent detected: %s from IP: %s", 
                          user_agent, request.environ.get('REMOTE_ADDR', 'unknown'))
    
    # Check for SQL injection attempts in URL path
    if _is_sql_injection_attempt(request.path):
        _fault_log.error("SQL injection attempt in URL path blocked: %s from IP: %s", 
                        request.path, request.environ.get('REMOTE_ADDR', 'unknown'))
        from flask import abort
        abort(400)