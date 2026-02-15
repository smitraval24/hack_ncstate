import os
import sys
import time
import logging
from importlib.metadata import version

import requests
from flask import Blueprint, render_template, request, current_app
from sqlalchemy import text

from config.settings import DEBUG, ENABLE_FAULT_INJECTION
from hello.extensions import db

page = Blueprint("page", __name__, template_folder="templates")


@page.get("/")
def home():
    return render_template(
        "page/home.html",
        flask_ver=version("flask"),
        python_ver=os.environ["PYTHON_VERSION"],
        debug=DEBUG,
        enable_fault_injection=ENABLE_FAULT_INJECTION,
    )


@page.get("/test-fault")
def test_fault():
    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=os.environ["PYTHON_VERSION"],
        debug=DEBUG,
        enable_fault_injection=True,
    )


@page.post("/test-fault/run")
def test_fault_run():
    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}

    if ENABLE_FAULT_INJECTION:
        result = {"status": "ok", "error_code": None}
    else:
        result = {"status": "ok", "error_code": None}

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=os.environ["PYTHON_VERSION"],
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (500 if result["status"] == "error" else 200)


@page.post("/test-fault/external-api")
def test_fault_external_api():
    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}

    start = time.time()

    # Initialize circuit breaker variables in the application context if they don't exist
    if not hasattr(current_app, 'circuit_open'):
        current_app.circuit_open = False
        current_app.circuit_open_time = None
        current_app.failure_count = 0

    circuit_breaker_timeout = 60  # seconds
    failure_threshold = 3

    try:
        mock_api_base = os.environ.get("MOCK_API_BASE_URL", "http://mock_api:5001")
        max_retries = 3
        retry_delay = 1  # seconds

        if current_app.circuit_open and current_app.circuit_open_time and time.time() - current_app.circuit_open_time < circuit_breaker_timeout:
            result = {
                "status": "error",
                "error_code": error_code,
                "detail": "circuit_breaker_open",
                "latency": "0.00s",
            }
            return render_template(
                "page/test_fault.html",
                flask_ver=version("flask"),
                python_ver=os.environ["PYTHON_VERSION"],
                debug=DEBUG,
                enable_fault_injection=True,
                result=result,
            ), 503  # Service Unavailable

        for attempt in range(max_retries):
            try:
                r = requests.get(f"{mock_api_base}/data", timeout=3)
                latency = time.time() - start

                from flask import current_app

                current_app.logger.info(f"external_call_latency={latency:.2f}")

                r.raise_for_status()
                result = {
                    "status": "ok",
                    "error_code": None,
                    "data": r.json(),
                    "latency": f"{latency:.2f}s",
                }
                # Reset failure count upon success
                current_app.failure_count = 0
                break  # If successful, break the retry loop
            except requests.exceptions.RequestException as e:
                current_app.failure_count += 1
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    from flask import current_app

                    current_app.logger.warning(f"Retrying after exception: {e}")
                else:
                    raise  # If all retries failed, raise the exception

    except requests.exceptions.Timeout:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "timeout",
            "latency": f"{latency:.2f}s",
        }

        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=external_timeout latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)

        from flask import current_app

        current_app.logger.error(msg)

    except requests.exceptions.HTTPError:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "upstream_500",
            "latency": f"{latency:.2f}s",
        }

        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=upstream_failure latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)

        from flask import current_app

        current_app.logger.error(msg)

    except requests.exceptions.ConnectionError:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "connection_refused",
            "latency": f"{latency:.2f}s",
        }

        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=connection_error latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)

        from flask import current_app

        current_app.logger.error(msg)

    except Exception as e:
        # General exception handling
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": str(e),
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=unhandled_exception latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        from flask import current_app

        current_app.logger.error(msg)

    finally:
        if result["status"] == "error" and result["error_code"] == error_code:
            if current_app.failure_count >= failure_threshold:
                current_app.circuit_open = True
                current_app.circuit_open_time = time.time()
                from flask import current_app

                current_app.logger.warning("Circuit breaker opened")


    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=os.environ["PYTHON_VERSION"],
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (504 if result["status"] == "error" else 200)


@page.post("/test-fault/db-timeout")
def test_fault_db_timeout():
    error_code = "FAULT_DB_TIMEOUT"
    result = {"status": "ok", "error_code": None}

    start = time.time()

    # Simulate a database timeout by sleeping for 5 seconds
    time.sleep(5)

    latency = time.time() - start
    result = {"status": "ok", "error_code": None, "latency": f"{latency:.2f}s"}

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=os.environ["PYTHON_VERSION"],
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), 200