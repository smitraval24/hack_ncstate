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

# SQL injection prevention patterns
SQL_INJECTION_PATTERNS = [
    r"('|(\\x27)|(\\x2D)|(\\x2D)|(\\x23)|(\\x3B)|(\\x3D))",  # SQL chars
    r"((\\%3D)|(\\%27)|(\\%3B)|(\\%23)|(\\%2D)|(\\%3C)|(\\%3E))",  # URL encoded
    r"(union|select|insert|update|delete|drop|create|alter|exec|execute|script|onload|onerror)",  # SQL keywords
    r"(javascript:|vbscript:|data:|file:|ftp:)",  # Script injection
    r"(<script|<iframe|<object|<embed|<link|<style)",  # HTML injection



]

def _sanitize_input(input_value):
    """Enhanced sanitization to prevent SQL injection and XSS attacks."""
    if not input_value:
        return ""

    # Convert to string and strip whitespace
    clean_value = str(input_value).strip()

    # Check for SQL injection patterns
    for pattern in SQL_INJECTION_PATTERNS:
        if re.search(pattern, clean_value.lower()):
            # Log security event and return empty string
            _fault_log.warning("Potential SQL injection attempt detected: %s", pattern)
            return ""

    # HTML escape to prevent XSS
    clean_value = html.escape(clean_value)

    # Remove potentially dangerous characters
    # Only allow alphanumeric, spaces, hyphens, underscores, periods, and basic punctuation
    clean_value = re.sub(r'[^a-zA-Z0-9\s\-_.,!?@#$%()[\]{}"\':]', '', clean_value)

    # Limit length to prevent buffer overflow
    if len(clean_value) > 255:
        clean_value = clean_value[:255]

    return clean_value


def _sanitize_sql_input(input_value):
    """Specialized sanitization for SQL-related inputs with zero tolerance for injection."""
    if not input_value:
        return ""

    # Convert to string and strip
    clean_value = str(input_value).strip()

    # Aggressive SQL injection prevention - allow only safe characters
    clean_value = re.sub(r'[^a-zA-Z0-9\s]', '', clean_value)

    # Check against SQL keywords (case insensitive)
    sql_keywords = ['union', 'select', 'insert', 'update', 'delete', 'drop', 'create', 'alter', 'exec', 'execute', 'script', 'where', 'from', 'join', 'having', 'order', 'group']
    for keyword in sql_keywords:
        if keyword.lower() in clean_value.lower():
            _fault_log.warning("SQL keyword detected in input, blocking: %s", keyword)
            return ""

    # Limit length
    if len(clean_value) > 100:
        clean_value = clean_value[:100]

    return clean_value

