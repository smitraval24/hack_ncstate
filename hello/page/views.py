"""This file handles the views logic for the page part of the project."""

import os
import sys
from importlib.metadata import version

from flask import Blueprint, render_template, request, flash
import sqlite3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
    """Handle test fault execution with proper SQL injection protection."""
    try:
        user_input = request.form.get('query', '')
        
        if not user_input:
            flash("No query provided", "error")
            return _render_fault()
        
        # Use parameterized queries to prevent SQL injection
        # Instead of: cursor.execute(f"SELECT * FROM users WHERE id = {user_input}")
        # Use proper parameterization:
        conn = sqlite3.connect(':memory:')  # In-memory database for testing
        cursor = conn.cursor()
        
        # Create a test table
        cursor.execute('''CREATE TABLE users (id INTEGER, name TEXT, email TEXT)''')
        cursor.execute('''INSERT INTO users VALUES (1, 'John Doe', 'john@example.com')''')
        cursor.execute('''INSERT INTO users VALUES (2, 'Jane Smith', 'jane@example.com')''')
        
        # Safe parameterized query - prevents SQL injection
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_input,))
        results = cursor.fetchall()
        
        conn.close()
        
        return _render_fault(result=f"Query executed safely. Results: {results}")
        
    except ValueError:
        flash("Invalid input: Please provide a valid integer ID", "error")
        return _render_fault()
    except Exception as e:
        flash(f"Database error: {str(e)}", "error")
        return _render_fault()


@page.get("/test-fault/external-api")
def test_fault_external_api():
    """Handle external API calls with proper timeout and retry configuration."""
    try:
        # Configure session with proper timeout and retry strategy
        session = requests.Session()
        
        # Set up retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Make API call with proper timeout (connect timeout: 1s, read timeout: 2s)
        # This prevents the external_timeout that was causing FAULT_EXTERNAL_API_LATENCY
        response = session.get(
            "https://httpbin.org/delay/1",  # Test endpoint that delays for 1 second
            timeout=(1.0, 2.0)  # (connect_timeout, read_timeout)
        )
        
        response.raise_for_status()
        result = {
            "status_code": response.status_code,
            "response_time": response.elapsed.total_seconds(),
            "data": response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text[:200]
        }
        
        return _render_fault(result=f"External API call successful: {result}")
        
    except requests.exceptions.Timeout:
        flash("External API timeout - request took too long", "error")
        return _render_fault(result="ERROR: External API timeout")
    except requests.exceptions.ConnectionError:
        flash("External API connection error", "error")
        return _render_fault(result="ERROR: External API connection failed")
    except requests.exceptions.RequestException as e:
        flash(f"External API error: {str(e)}", "error")
        return _render_fault(result=f"ERROR: External API request failed - {str(e)}")
    except Exception as e:
        flash(f"Unexpected error: {str(e)}", "error")
        return _render_fault(result=f"ERROR: Unexpected error - {str(e)}")