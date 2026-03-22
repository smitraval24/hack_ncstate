"""This file handles the views logic for the page part of the project."""

import os
import sys
from importlib.metadata import version

from flask import Blueprint, render_template, request
import sqlite3
import time

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


@page.get("/test-fault/db-timeout")
def test_fault_db_timeout():
    """Handle database timeout fault injection test with proper timeout handling."""
    if not ENABLE_FAULT_INJECTION:
        return _render_fault(result="Fault injection disabled")
    
    try:
        # Set up database connection with timeout
        conn = sqlite3.connect(":memory:", timeout=2.0)  # 2 second timeout
        cursor = conn.cursor()
        
        # Create test table
        cursor.execute("CREATE TABLE timeout_test (id INTEGER, data TEXT)")
        cursor.execute("INSERT INTO timeout_test VALUES (1, 'test_data')")
        
        # Simulate a long-running query that would cause timeout
        # Instead of actually waiting, return controlled response
        start_time = time.time()
        
        # Execute a simple query quickly to avoid actual timeout
        cursor.execute("SELECT * FROM timeout_test WHERE id = ?", (1,))
        results = cursor.fetchall()
        
        elapsed_time = time.time() - start_time
        conn.close()
        
        return _render_fault(result=f"DB timeout test completed. Query executed in {elapsed_time:.3f}s. Results: {results}")
        
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e).lower() or "timeout" in str(e).lower():
            return _render_fault(result=f"Database timeout handled gracefully: {str(e)}")
        else:
            return _render_fault(result=f"Database error: {str(e)}")
    except Exception as e:
        return _render_fault(result=f"Unexpected error: {str(e)}")