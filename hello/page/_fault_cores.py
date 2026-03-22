"""Core fault injection logic.

This module contains the actual broken operations that trigger faults.
These are INTENTIONALLY broken — do NOT fix them.
"""

from sqlalchemy import text

from hello.extensions import db


def run_bad_sql():
    """Execute intentionally malformed SQL. Always raises an exception."""
    db.session.execute(text("SELECT FROM"))


def run_db_timeout():
    """Execute pg_sleep(5) with no app-level statement_timeout.
    Relies on DB-level or pool-level timeout to trigger the fault.
    Always causes 5+ second delay, often times out.
    """
    db.session.execute(text("SELECT pg_sleep(5);"))


def call_external_api():
    """Call mock API with a tight 3s timeout.
    The mock API has 60% chance of 2-8s delay and 30% chance of HTTP 500.
    Fails ~70% of the time.
    """
    import requests
    r = requests.get("http://mock_api:5001/data", timeout=3)
    r.raise_for_status()
    return r
