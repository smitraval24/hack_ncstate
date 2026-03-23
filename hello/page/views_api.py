"""Fault handler for FAULT_EXTERNAL_API_LATENCY.

This is the ONLY file the self-healing loop may edit when remediating
this fault code.  The route is registered on the page blueprint.

"""

import os
import sys
import time

import requests
from flask import current_app

from config.settings import ENABLE_FAULT_INJECTION
from hello.incident.live_store import (
    create_incident as create_live_incident,
)
from hello.page.views import _is_fault_verification_request, _render_fault, page


def _record_external_api_incident(reason: str, latency: float) -> None:
    """Persist a live incident for the external API fault route."""
    create_live_incident(
        error_code="FAULT_EXTERNAL_API_LATENCY",
        route="/test-fault/external-api",
        reason=reason,
        latency=latency,
    )


def _make_external_api_call_with_retry(url: str, max_retries: int = 2) -> tuple:
    """Make external API call with retry logic and improved timeout handling."""
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            start = time.time()
            # Increased timeout from 3s to 10s to handle slower external APIs
            # Added connection timeout (5s) and read timeout (10s) for better control
            r = requests.get(url, timeout=(5, 10))
            latency = time.time() - start
            current_app.logger.info(f"external_call_latency={latency:.2f} attempt={attempt + 1}")
            r.raise_for_status()
            return r, latency, None
            
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, 
                requests.exceptions.HTTPError) as e:
            last_exception = e
            latency = time.time() - start
            current_app.logger.warning(
                f"API call attempt {attempt + 1}/{max_retries + 1} failed: {type(e).__name__} "
                f"latency={latency:.2f}s"
            )
            
            # Don't retry on the last attempt
            if attempt < max_retries:
                # Exponential backoff: wait 0.5s, then 1s
                wait_time = 0.5 * (2 ** attempt)
                time.sleep(wait_time)
            else:
                return None, latency, last_exception
    
    return None, time.time() - start, last_exception


@page.post("/test-fault/external-api")
def test_fault_external_api():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}
    verification_only = _is_fault_verification_request()
    mock_api_base_url = os.getenv("MOCK_API_BASE_URL", "http://mock_api:5001").rstrip("/")

    # Use retry logic for improved resilience
    r, latency, exception = _make_external_api_call_with_retry(f"{mock_api_base_url}/data")
    
    if exception is not None:
        # Handle different exception types
        if isinstance(exception, requests.exceptions.Timeout):
            detail = "timeout"
            reason = "external_timeout"
        elif isinstance(exception, requests.exceptions.HTTPError):
            detail = "upstream_500"
            reason = "upstream_failure"
        elif isinstance(exception, requests.exceptions.ConnectionError):
            detail = "connection_refused"
            reason = "connection_error"
        else:
            detail = "unknown_error"
            reason = "unknown_error"
        
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": detail,
            "latency": f"{latency:.2f}s",
        }
        
        if not verification_only:
            msg = (
                f"{error_code} route=/test-fault/external-api "
                f"reason={reason} latency={latency:.2f}"
            )
            print(msg, file=sys.stderr)
            current_app.logger.error(msg)

            try:
                _record_external_api_incident(reason, latency)
            except Exception:
                current_app.logger.exception("Failed to create incident for %s", error_code)

        return _render_fault(result), 504

    # Successfully got response
    try:
        payload = r.json()
        if payload.get("value") != 42:
            result = {
                "status": "error",
                "error_code": error_code,
                "detail": "wrong_data",
                "latency": f"{latency:.2f}s",
                "data": payload,
            }
            if not verification_only:
                msg = (
                    f"{error_code} route=/test-fault/external-api "
                    f"reason=wrong_data latency={latency:.2f}"
                )
                print(msg, file=sys.stderr)
                current_app.logger.error(msg)

                try:
                    _record_external_api_incident("wrong_data", latency)
                except Exception:
                    current_app.logger.exception("Failed to create incident for %s", error_code)

            return _render_fault(result), 504

        result = {
            "status": "ok",
            "error_code": None,
            "data": payload,
            "latency": f"{latency:.2f}s",
        }

    except ValueError as e:
        # Handle JSON parsing errors
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "invalid_json",
            "latency": f"{latency:.2f}s",
        }
        if not verification_only:
            msg = (
                f"{error_code} route=/test-fault/external-api "
                f"reason=invalid_response latency={latency:.2f}"
            )
            print(msg, file=sys.stderr)
            current_app.logger.error(msg)

            try:
                _record_external_api_incident("invalid_response", latency)
            except Exception:
                current_app.logger.exception("Failed to create incident for %s", error_code)

        return _render_fault(result), 504

    return _render_fault(result), (504 if result["status"] == "error" else 200)