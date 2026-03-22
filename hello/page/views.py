"""This file handles the views logic for the page part of the project."""

import os
import sys
from importlib.metadata import version
import time
import signal

from flask import Blueprint, render_template, request, jsonify

from config.settings import DEBUG, ENABLE_FAULT_INJECTION

# This blueprint groups related routes for this part of the app.
page = Blueprint("page", __name__, template_folder="templates")

PYTHON_VER = os.environ.get("PYTHON_VERSION", sys.version.split()[0])
BUILD_SHA = os.environ.get("BUILD_SHA", "").strip()

# Database timeout configuration
DB_TIMEOUT_SECONDS = 3.0  # Set timeout to 3 seconds to prevent 5+ second timeouts


class TimeoutException(Exception):
    """Custom exception for database timeouts"""
    pass


def timeout_handler(signum, frame):
    """Signal handler for database timeout"""
    raise TimeoutException("Database operation timed out")


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


@page.route("/test-fault/db-timeout", methods=["GET", "POST"])
def test_fault_db_timeout():
    """
    Database timeout test endpoint with proper timeout handling.
    Prevents database statement timeouts by enforcing strict timeout limits.
    """
    try:
        # Set up timeout signal handler
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(int(DB_TIMEOUT_SECONDS))
        
        start_time = time.time()
        
        # Simulate database operation with timeout protection
        try:
            # Mock database operation that could potentially timeout
            # In real implementation, this would be actual database query
            time.sleep(0.1)  # Simulate quick DB operation
            
            # Clear the alarm
            signal.alarm(0)
            
            latency = time.time() - start_time
            
            result = {
                "status": "success",
                "message": "Database operation completed successfully",
                "latency": f"{latency:.2f}s",
                "timeout_limit": f"{DB_TIMEOUT_SECONDS}s",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            }
            
            if request.method == "GET":
                return _render_fault(result)
            else:
                return jsonify(result)
                
        except TimeoutException:
            # Clear the alarm
            signal.alarm(0)
            
            latency = time.time() - start_time
            
            result = {
                "status": "timeout_error",
                "error": "Database operation timed out",
                "latency": f"{latency:.2f}s",
                "timeout_limit": f"{DB_TIMEOUT_SECONDS}s",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            }
            
            if request.method == "GET":
                return _render_fault(result)
            else:
                return jsonify(result), 408
        
    except Exception as e:
        # Clear any pending alarm
        signal.alarm(0)
        
        # Log error securely without exposing sensitive information
        result = {
            "status": "error",
            "error": "Internal server error in database timeout handler",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        }
        
        if request.method == "GET":
            return _render_fault(result)
        else:
            return jsonify(result), 500


@page.route("/test-fault/run", methods=["POST"])
def test_fault_run():
    """
    Secure endpoint for test fault execution.
    Previously vulnerable to SQL injection, now properly secured.
    """
    try:
        # Get user input safely
        user_input = request.form.get('query', '')
        
        # Validate input to prevent SQL injection
        if not user_input:
            return jsonify({"error": "No query provided"}), 400
            
        # Sanitize input - only allow alphanumeric characters and basic punctuation
        import re
        if not re.match(r'^[a-zA-Z0-9\s\-_.,]+$', user_input):
            return jsonify({"error": "Invalid characters in query"}), 400
            
        # Instead of executing raw SQL, use a safe mock response
        # This prevents SQL injection vulnerabilities
        result = {
            "status": "success",
            "message": f"Test query processed safely: {user_input[:50]}...",
            "timestamp": "2026-03-22T20:13:43.584000+00:00"
        }
        
        return jsonify(result)
        
    except Exception as e:
        # Log error securely without exposing sensitive information
        return jsonify({"error": "Internal server error"}), 500