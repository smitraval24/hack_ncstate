"""Middleware for automatic request/response observability."""

import logging
import time
from flask import g, request

from hello.observability.metrics import metrics_collector

logger = logging.getLogger(__name__)


class ObservabilityMiddleware:
    """Middleware to track request/response metrics and logging."""

    def __init__(self, app=None):
        """
        Initialize the observability middleware.

        Args:
            app: Flask application instance
        """
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        """
        Register middleware hooks with Flask app.

        Args:
            app: Flask application instance
        """
        app.before_request(self.before_request)
        app.after_request(self.after_request)
        app.teardown_request(self.teardown_request)

    @staticmethod
    def before_request():
        """Record the start time of the request."""
        g.start_time = time.time()
        g.request_id = request.headers.get("X-Request-ID", "unknown")

        logger.info(
            "Request started",
            extra={
                "request_id": g.request_id,
                "method": request.method,
                "path": request.path,
                "endpoint": request.endpoint,
                "remote_addr": request.remote_addr,
                "user_agent": request.headers.get("User-Agent", "unknown"),
            },
        )

    @staticmethod
    def after_request(response):
        """
        Record request completion metrics and logs.

        Args:
            response: Flask response object

        Returns:
            Flask response object
        """
        if hasattr(g, "start_time"):
            latency_ms = (time.time() - g.start_time) * 1000

            # Log request completion
            logger.info(
                "Request completed",
                extra={
                    "request_id": getattr(g, "request_id", "unknown"),
                    "method": request.method,
                    "path": request.path,
                    "endpoint": request.endpoint,
                    "status_code": response.status_code,
                    "latency_ms": round(latency_ms, 2),
                    "content_length": response.content_length,
                },
            )

            # Record metrics
            endpoint = request.endpoint or request.path
            metrics_collector.record_request(
                endpoint=endpoint,
                method=request.method,
                status_code=response.status_code,
                latency_ms=latency_ms,
            )

            # Add custom headers for debugging
            response.headers["X-Request-ID"] = getattr(
                g, "request_id", "unknown"
            )
            response.headers["X-Response-Time"] = str(round(latency_ms, 2))

        return response

    @staticmethod
    def teardown_request(exception=None):
        """
        Log any exceptions that occurred during request processing.

        Args:
            exception: Exception that occurred (if any)
        """
        if exception:
            latency_ms = (
                (time.time() - g.start_time) * 1000
                if hasattr(g, "start_time")
                else 0
            )

            logger.error(
                "Request failed with exception",
                extra={
                    "request_id": getattr(g, "request_id", "unknown"),
                    "method": request.method,
                    "path": request.path,
                    "endpoint": request.endpoint,
                    "exception": str(exception),
                    "exception_type": type(exception).__name__,
                    "latency_ms": round(latency_ms, 2),
                },
                exc_info=True,
            )

            # Record error metric
            endpoint = request.endpoint or request.path
            metrics_collector.record_request(
                endpoint=endpoint,
                method=request.method,
                status_code=500,
                latency_ms=latency_ms,
            )
