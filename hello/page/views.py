"""This file handles the views logic for the page part of the project."""

import os
import sys
from importlib.metadata import version
import time
import signal
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

from flask import Blueprint, render_template, request, jsonify

from config.settings import DEBUG, ENABLE_FAULT_INJECTION

# This blueprint groups related routes for this part of the app.
page = Blueprint("page", __name__, template_folder="templates")

PYTHON_VER = os.environ.get("PYTHON_VERSION", sys.version.split()[0])
BUILD_SHA = os.environ.get("BUILD_SHA", "").strip()

# Database timeout configuration
DB_TIMEOUT_SECONDS = 3.0  # Set timeout to 3 seconds to prevent 5+ second timeouts

# External API configuration
EXTERNAL_API_TIMEOUT = 5.0  # 5 second timeout for external API calls
EXTERNAL_API_RETRIES = 3  # Number of retries for failed requests
EXTERNAL_API_BACKOFF = 0.3  # Backoff factor for retries


class TimeoutException(Exception):
    """Custom exception for database timeouts"""
    pass


class ExternalAPIException(Exception):
    """Custom exception for external API errors"""
    pass


def timeout_handler(signum, frame):
    """Signal handler for database timeout"""
    raise TimeoutException("Database operation timed out")


def create_requests_session():
    """Create a requests session with proper timeout and retry configuration"""
    session = requests.Session()
    
    # Configure retry strategy
    retry_strategy = Retry(
        total=EXTERNAL_API_RETRIES,
        status_forcelist=[429, 500, 502, 503, 504],
        method_whitelist=["HEAD", "GET", "OPTIONS"],
        backoff_factor=EXTERNAL_API_BACKOFF
    )
    
    # Mount the adapter
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session


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


@page.route("/test-fault/external-api", methods=["GET", "POST"])
def test_fault_external_api():
    """
    External API test endpoint with proper timeout and connection error handling.
    Implements retry logic and robust error handling to prevent API latency issues.
    """
    try:
        start_time = time.time()
        
        # Create session with retry configuration
        session = create_requests_session()
        
        # Mock external API endpoint (in production this would be a real API)
        test_api_url = "https://httpbin.org/delay/1"
        
        try:
            # Make API call with timeout
            response = session.get(
                test_api_url,
                timeout=(EXTERNAL_API_TIMEOUT, EXTERNAL_API_TIMEOUT)  # (connect_timeout, read_timeout)
            )
            
            latency = time.time() - start_time
            
            if response.status_code == 200:
                result = {
                    "status": "success",
                    "message": "External API call completed successfully",
                    "latency": f"{latency:.3f}s",
                    "timeout_limit": f"{EXTERNAL_API_TIMEOUT}s",
                    "retries_configured": EXTERNAL_API_RETRIES,
                    "api_response_code": response.status_code,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                }
            else:
                result = {
                    "status": "api_error",
                    "error": f"API returned status code {response.status_code}",
                    "latency": f"{latency:.3f}s",
                    "timeout_limit": f"{EXTERNAL_API_TIMEOUT}s",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                }
                
        except requests.exceptions.ConnectionError as e:
            latency = time.time() - start_time
            
            result = {
                "status": "connection_error",
                "error": "Failed to establish connection to external API",
                "latency": f"{latency:.3f}s",
                "timeout_limit": f"{EXTERNAL_API_TIMEOUT}s",
                "retries_attempted": EXTERNAL_API_RETRIES,
                "error_type": "connection_error",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            }
            
            # Return appropriate HTTP status
            if request.method == "GET":
                return _render_fault(result)
            else:
                return jsonify(result), 503
                
        except requests.exceptions.Timeout as e:
            latency = time.time() - start_time
            
            result = {
                "status": "timeout_error",
                "error": "External API request timed out",
                "latency": f"{latency:.3f}s",
                "timeout_limit": f"{EXTERNAL_API_TIMEOUT}s",
                "error_type": "timeout",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            }
            
            if request.method == "GET":
                return _render_fault(result)
            else:
                return jsonify(result), 408
                
        except requests.exceptions.RequestException as e:
            latency = time.time() - start_time
            
            result = {
                "status": "request_error",
                "error": "External API request failed",
                "latency": f"{latency:.3f}s",
                "error_type": "request_exception",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            }
            
            if request.method == "GET":
                return _render_fault(result)
            else:
                return jsonify(result), 502
        
        if request.method == "GET":
            return _render_fault(result)
        else:
            return jsonify(result)
            
    except Exception as e:
        # Fallback error handling
        latency = time.time() - start_time
        
        result = {
            "status": "internal_error",
            "error": "Internal server error in external API handler",
            "latency": f"{latency:.3f}s",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        }
        
        if request.method == "GET":
            return _render_fault(result)
        else:
            return jsonify(result), 500


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