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
from hello.page.views import page, _render_fault


def _record_external_api_incident(reason: str, latency: float) -> None:
    """Persist a live incident for the external API fault route."""
    create_live_incident(
        error_code="FAULT_EXTERNAL_API_LATENCY",
        route="/test-fault/external-api",
        reason=reason,
        latency=latency,
    )


@page.post("/test-fault/external-api")
def test_fault_external_api():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}
    mock_api_base_url = os.getenv("MOCK_API_BASE_URL", "http://mock_api:5001").rstrip("/")

    start = time.time()

    try:
        # INTENTIONAL BUG: 3s timeout against mock API with a high chance of
        # latency or malformed data.
        r = requests.get(f"{mock_api_base_url}/data", timeout=3)
        latency = time.time() - start
        current_app.logger.info(f"external_call_latency={latency:.2f}")
        r.raise_for_status()
        payload = r.json()
        if payload.get("value") != 42:
            result = {
                "status": "error",
                "error_code": error_code,
                "detail": "wrong_data",
                "latency": f"{latency:.2f}s",
                "data": payload,
            }
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
        current_app.logger.error(msg)

        try:
            _record_external_api_incident("external_timeout", latency)
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

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
        current_app.logger.error(msg)

        try:
            _record_external_api_incident("upstream_failure", latency)
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

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
        current_app.logger.error(msg)

        try:
            _record_external_api_incident("connection_error", latency)
        except Exception:
            current_app.logger.exception("Failed to create incident for %s", error_code)

    return _render_fault(result), (504 if result["status"] == "error" else 200)
