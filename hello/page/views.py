"""This file handles the views logic for the page part of the project."""

import os
import sys
import time
from importlib.metadata import version

import requests
from flask import Blueprint, render_template, current_app
from sqlalchemy import text
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import DEBUG, ENABLE_FAULT_INJECTION
from hello.extensions import db
from hello.incident.live_store import (
    create_incident as create_live_incident,
)

# This blueprint groups related routes for this part of the app.
page = Blueprint("page", __name__, template_folder="templates")

PYTHON_VER = os.environ.get("PYTHON_VERSION", sys.version.split()[0])


# Enhanced circuit breaker implementation with better latency handling
class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60, latency_threshold=10.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.latency_threshold = latency_threshold
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN
        self.consecutive_successes = 0
    
    def call(self, func, *args, **kwargs):
        if self.state == 'OPEN':
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = 'HALF_OPEN'
                self.consecutive_successes = 0
            else:
                raise Exception("Circuit breaker is OPEN - too many recent failures")
        
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            latency = time.time() - start_time
            
            # Consider high latency as a soft failure
            if latency > self.latency_threshold:
                self._on_slow_response()
            else:
                self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise e
    
    def _on_success(self):
        if self.state == 'HALF_OPEN':
            self.consecutive_successes += 1
            if self.consecutive_successes >= 3:
                self.state = 'CLOSED'
                self.failure_count = 0
        elif self.state == 'CLOSED':
            self.failure_count = max(0, self.failure_count - 1)
    
    def _on_slow_response(self):
        self.failure_count += 0.5  # Count slow responses as half failures
        if self.failure_count >= self.failure_threshold:
            self.state = 'OPEN'
            self.last_failure_time = time.time()
    
    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = 'OPEN'

# Global circuit breaker instance with enhanced settings
api_circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60, latency_threshold=5.0)

def get_requests_session_with_retries():
    """Create a requests session with enhanced retry strategy and timeouts."""
    session = requests.Session()
    
    # Enhanced retry strategy with exponential backoff
    retry_strategy = Retry(
        total=5,  # Increased total retries
        backoff_factor=1.0,  # More aggressive backoff
        status_forcelist=[408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524],
        connect=3,  # Connection retries
        read=3,    # Read retries
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],  # Explicit method list
        raise_on_status=False,  # Don't raise immediately, let us handle status codes
    )
    
    # Enhanced adapter with connection pooling
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=10,
        pool_maxsize=20,
        pool_block=False
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # Set default headers for better connection handling
    session.headers.update({
        'Connection': 'keep-alive',
        'Keep-Alive': 'timeout=30, max=100',
        'User-Agent': 'hello-app/1.0'
    })
    
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


@page.get("/test-fault")
def test_fault():
    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
    )


@page.post("/test-fault/run")
def test_fault_run():
    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}

    if not ENABLE_FAULT_INJECTION:
        return "", 404

    try:
        # INTENTIONAL: malformed SQL — must always fail
        db.session.execute(text("SELECT FROM"))
    except Exception as e:
        db.session.rollback()
        result = {"status": "error", "error_code": error_code}

        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=invalid_sql_executed"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/run",
                reason="invalid_sql_executed",
            )
        except Exception:
            pass

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (500 if result["status"] == "error" else 200)


def make_external_api_call():
    """Make external API call with enhanced error handling and timeouts."""
    session = get_requests_session_with_retries()
    
    # More generous timeouts to handle network variations
    # Connect timeout: 10s, Read timeout: 20s
    response = session.get(
        "http://mock_api:5001/data", 
        timeout=(10, 20)
    )
    
    # Check status code manually since we disabled raise_on_status
    if response.status_code >= 400:
        response.raise_for_status()
    
    return response


@page.post("/test-fault/external-api")
def test_fault_external_api():
    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}

    if not ENABLE_FAULT_INJECTION:
        return "", 404

    start = time.time()

    try:
        # Use enhanced circuit breaker pattern with latency awareness
        response = api_circuit_breaker.call(make_external_api_call)
        latency = time.time() - start
        current_app.logger.info(f"external_call_success latency={latency:.3f}s")
        
        result = {
            "status": "ok",
            "error_code": None,
            "data": response.json(),
            "latency": f"{latency:.3f}s",
            "circuit_breaker_state": api_circuit_breaker.state,
        }

    except requests.exceptions.Timeout:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "timeout",
            "latency": f"{latency:.3f}s",
            "circuit_breaker_state": api_circuit_breaker.state,
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=external_timeout latency={latency:.3f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="external_timeout",
                latency=latency,
            )
        except Exception:
            pass

    except requests.exceptions.HTTPError as e:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": f"upstream_{e.response.status_code if e.response else 'unknown'}",
            "latency": f"{latency:.3f}s",
            "circuit_breaker_state": api_circuit_breaker.state,
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=upstream_failure latency={latency:.3f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="upstream_failure",
                latency=latency,
            )
        except Exception:
            pass

    except requests.exceptions.ConnectionError:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "connection_refused",
            "latency": f"{latency:.3f}s",
            "circuit_breaker_state": api_circuit_breaker.state,
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=connection_error latency={latency:.3f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="connection_error",
                latency=latency,
            )
        except Exception:
            pass

    except Exception as e:
        # Handle circuit breaker and other exceptions
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": f"circuit_breaker_or_unexpected: {str(e)[:100]}",
            "latency": f"{latency:.3f}s",
            "circuit_breaker_state": api_circuit_breaker.state,
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=circuit_breaker_open_or_error latency={latency:.3f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/external-api",
                reason="circuit_breaker_open_or_error",
                latency=latency,
            )
        except Exception:
            pass

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (504 if result["status"] == "error" else 200)


@page.post("/test-fault/db-timeout")
def test_fault_db_timeout():
    error_code = "FAULT_DB_TIMEOUT"
    result = {"status": "ok", "error_code": None}

    if not ENABLE_FAULT_INJECTION:
        return "", 404

    start = time.time()

    try:
        # INTENTIONAL: Set a 2s statement timeout then sleep for 5s
        # This guarantees the query will be cancelled by PostgreSQL
        db.session.execute(text("SET LOCAL statement_timeout = '2s';"))
        db.session.execute(text("SELECT pg_sleep(5);"))
        latency = time.time() - start
        result = {
            "status": "ok",
            "error_code": None,
            "latency": f"{latency:.2f}s",
        }
    except Exception as e:
        db.session.rollback()
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": str(e)[:200],
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/db-timeout "
            f"reason=db_timeout_or_pool_exhaustion latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(f"db_error={e!s}")

        try:
            create_live_incident(
                error_code=error_code,
                route="/test-fault/db-timeout",
                reason="db_timeout_or_pool_exhaustion",
                latency=latency,
            )
        except Exception:
            pass

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (500 if result["status"] == "error" else 200)