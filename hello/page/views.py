"""This file handles the views logic for the page part of the project."""

import os
import sys
import time
import requests
from importlib.metadata import version

from flask import Blueprint, render_template, request
import sqlite3

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
def test_fault_run():
    """Handle fault injection test with proper SQL injection protection."""
    if not ENABLE_FAULT_INJECTION:
        return _render_fault(result="Fault injection disabled")
    
    # Get user input safely
    user_input = request.form.get("query", "")
    
    try:
        # Use parameterized queries to prevent SQL injection
        conn = sqlite3.connect(":memory:")  # In-memory database for testing
        cursor = conn.cursor()
        
        # Create a test table
        cursor.execute("CREATE TABLE test_table (id INTEGER, name TEXT)")
        cursor.execute("INSERT INTO test_table VALUES (1, 'test_data')")
        
        # Use parameterized query instead of string concatenation
        # This prevents SQL injection by treating user input as data, not code
        safe_query = "SELECT * FROM test_table WHERE name = ?"
        cursor.execute(safe_query, (user_input,))
        
        results = cursor.fetchall()
        conn.close()
        
        return _render_fault(result=f"Query executed safely. Results: {results}")
        
    except Exception as e:
        return _render_fault(result=f"Error: {str(e)}")


@page.get("/test-fault/external-api")
def test_fault_external_api():
    """Handle external API calls with proper timeout and retry logic."""
    if not ENABLE_FAULT_INJECTION:
        return _render_fault(result="Fault injection disabled")
    
    start_time = time.time()
    max_retries = 3
    timeout_seconds = 5
    retry_delay = 0.5
    
    for attempt in range(max_retries):
        try:
            # Make external API call with proper timeout and error handling
            response = requests.get(
                "https://httpbin.org/delay/1",  # Test endpoint that introduces delay
                timeout=timeout_seconds,
                headers={
                    'User-Agent': 'cream-app/1.0',
                    'Accept': 'application/json'
                }
            )
            
            # Check if response is successful
            response.raise_for_status()
            
            end_time = time.time()
            latency = end_time - start_time
            
            return _render_fault(
                result=f"External API call successful. Latency: {latency:.3f}s, Status: {response.status_code}"
            )
            
        except requests.exceptions.Timeout:
            end_time = time.time()
            latency = end_time - start_time
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            else:
                return _render_fault(
                    result=f"FAULT_EXTERNAL_API_LATENCY: Timeout after {max_retries} attempts. Total latency: {latency:.3f}s"
                )
                
        except requests.exceptions.ConnectionError as e:
            end_time = time.time()
            latency = end_time - start_time
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            else:
                return _render_fault(
                    result=f"FAULT_EXTERNAL_API_LATENCY: Connection error after {max_retries} attempts. Reason: connection_error, Latency: {latency:.3f}s"
                )
                
        except requests.exceptions.HTTPError as e:
            end_time = time.time()
            latency = end_time - start_time
            
            return _render_fault(
                result=f"FAULT_EXTERNAL_API_LATENCY: HTTP error {e.response.status_code}. Latency: {latency:.3f}s"
            )
            
        except requests.exceptions.RequestException as e:
            end_time = time.time()
            latency = end_time - start_time
            
            return _render_fault(
                result=f"FAULT_EXTERNAL_API_LATENCY: Request failed - {str(e)}. Latency: {latency:.3f}s"
            )