"""This file handles the views logic for the page part of the project."""

import os
import sys
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