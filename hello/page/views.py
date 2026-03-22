"""This file handles the views logic for the page part of the project."""

import os
import sys
from importlib.metadata import version

from flask import Blueprint, render_template, request, flash
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