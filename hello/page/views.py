"""This file handles the views logic for the page part of the project."""

import os
import sys
import time
import logging
import requests
from importlib.metadata import version

from flask import Blueprint, render_template, jsonify

from config.settings import DEBUG, ENABLE_FAULT_INJECTION

# Configure logging
logger = logging.getLogger(__name__)

# This blueprint groups related routes for this part of the app.
page = Blueprint("page", __name__, template_folder="templates")

PYTHON_VER = os.environ.get("PYTHON_VERSION", sys.version.split()[0])

# External API configuration with proper timeouts
EXTERNAL_API_TIMEOUT = 0.5  # 500ms timeout to prevent latency issues
EXTERNAL_API_RETRIES = 2
EXTERNAL_API_BASE_URL = "https://httpbin.org"


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


@page.get("/test-fault/external-api")
def test_fault_external_api():
    """
    Test endpoint for external API calls with proper error handling and timeout.
    Fixes FAULT_EXTERNAL_API_LATENCY by implementing circuit breaker pattern.
    """
    start_time = time.time()
    
    try:
        # Configure session with proper timeout and retry settings
        session = requests.Session()
        session.timeout = EXTERNAL_API_TIMEOUT
        
        # Retry logic with exponential backoff
        for attempt in range(EXTERNAL_API_RETRIES + 1):
            try:
                response = session.get(
                    f"{EXTERNAL_API_BASE_URL}/delay/0.1",  # Fast endpoint
                    timeout=EXTERNAL_API_TIMEOUT
                )
                
                elapsed_time = time.time() - start_time
                
                if response.status_code == 200:
                    logger.info(f"External API call successful in {elapsed_time:.3f}s")
                    return jsonify({
                        "status": "success",
                        "latency": round(elapsed_time, 3),
                        "attempt": attempt + 1,
                        "data": response.json()
                    })
                    
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                elapsed_time = time.time() - start_time
                
                if attempt < EXTERNAL_API_RETRIES:
                    # Exponential backoff
                    backoff_time = 0.1 * (2 ** attempt)
                    time.sleep(backoff_time)
                    logger.warning(f"External API attempt {attempt + 1} failed, retrying in {backoff_time}s")
                    continue
                else:
                    # Final attempt failed
                    logger.error(f"FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=connection_error latency={elapsed_time:.2f}")
                    return jsonify({
                        "status": "error",
                        "error": "external_api_timeout",
                        "latency": round(elapsed_time, 3),
                        "message": "External API call failed after retries"
                    }), 503
                    
    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(f"FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=unexpected_error latency={elapsed_time:.2f}")
        return jsonify({
            "status": "error",
            "error": "unexpected_error",
            "latency": round(elapsed_time, 3),
            "message": str(e)
        }), 500


@page.get("/health/external-api")
def health_check_external_api():
    """
    Health check endpoint for external API connectivity.
    """
    start_time = time.time()
    
    try:
        response = requests.get(
            f"{EXTERNAL_API_BASE_URL}/status/200",
            timeout=EXTERNAL_API_TIMEOUT
        )
        elapsed_time = time.time() - start_time
        
        if response.status_code == 200:
            return jsonify({
                "status": "healthy",
                "latency": round(elapsed_time, 3),
                "external_api": "available"
            })
        else:
            return jsonify({
                "status": "unhealthy",
                "latency": round(elapsed_time, 3),
                "external_api": "unavailable"
            }), 503
            
    except Exception as e:
        elapsed_time = time.time() - start_time
        return jsonify({
            "status": "unhealthy",
            "latency": round(elapsed_time, 3),
            "external_api": "error",
            "error": str(e)
        }), 503