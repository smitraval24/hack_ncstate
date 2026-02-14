"""CloudWatch metrics collection for application monitoring."""

import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Collects and sends metrics to CloudWatch."""

    def __init__(self, namespace="HelloFlaskApp", enabled=None):
        """
        Initialize the metrics collector.

        Args:
            namespace: CloudWatch metrics namespace
            enabled: Whether metrics are enabled (defaults to env var)
        """
        self.namespace = namespace
        self.enabled = (
            enabled
            if enabled is not None
            else os.getenv("ENABLE_CLOUDWATCH_METRICS", "false").lower()
            == "true"
        )
        self.client = None

        if self.enabled:
            try:
                import boto3

                self.client = boto3.client(
                    "cloudwatch",
                    region_name=os.getenv("AWS_REGION", "us-east-1"),
                )
                logger.info(
                    "CloudWatch metrics enabled",
                    extra={"namespace": self.namespace},
                )
            except Exception as e:
                logger.error(
                    "Failed to initialize CloudWatch client",
                    extra={"error": str(e)},
                )
                self.enabled = False
        else:
            logger.info(
                "CloudWatch metrics disabled - metrics will be logged only"
            )

    def put_metric(
        self,
        metric_name: str,
        value: float,
        unit: str = "None",
        dimensions: Optional[dict] = None,
    ):
        """
        Send a metric to CloudWatch.

        Args:
            metric_name: Name of the metric
            value: Metric value
            unit: Unit of measurement (Count, Milliseconds, etc.)
            dimensions: Optional dimensions for the metric
        """
        dimensions = dimensions or {}

        # Always log metrics locally
        logger.info(
            f"Metric: {metric_name}",
            extra={
                "metric_name": metric_name,
                "value": value,
                "unit": unit,
                "dimensions": dimensions,
            },
        )

        # Send to CloudWatch if enabled
        if self.enabled and self.client:
            try:
                metric_data = {
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": unit,
                    "Timestamp": datetime.utcnow(),
                }

                if dimensions:
                    metric_data["Dimensions"] = [
                        {"Name": k, "Value": str(v)}
                        for k, v in dimensions.items()
                    ]

                self.client.put_metric_data(
                    Namespace=self.namespace, MetricData=[metric_data]
                )
            except Exception as e:
                logger.error(
                    "Failed to send metric to CloudWatch",
                    extra={
                        "metric_name": metric_name,
                        "error": str(e),
                    },
                )

    def record_request(
        self,
        endpoint: str,
        method: str,
        status_code: int,
        latency_ms: float,
    ):
        """
        Record metrics for an HTTP request.

        Args:
            endpoint: The request endpoint
            method: HTTP method
            status_code: HTTP status code
            latency_ms: Request latency in milliseconds
        """
        dimensions = {
            "Endpoint": endpoint,
            "Method": method,
            "StatusCode": str(status_code),
        }

        # Record latency
        self.put_metric(
            "RequestLatency",
            latency_ms,
            unit="Milliseconds",
            dimensions=dimensions,
        )

        # Record request count
        self.put_metric("RequestCount", 1, unit="Count", dimensions=dimensions)

        # Record errors (5xx)
        if 500 <= status_code < 600:
            self.put_metric(
                "ErrorCount", 1, unit="Count", dimensions=dimensions
            )

    def record_dependency_call(
        self, dependency: str, latency_ms: float, success: bool
    ):
        """
        Record metrics for a dependency call (DB, Redis, external API).

        Args:
            dependency: Name of the dependency
            latency_ms: Call latency in milliseconds
            success: Whether the call succeeded
        """
        dimensions = {"Dependency": dependency, "Success": str(success)}

        self.put_metric(
            "DependencyLatency",
            latency_ms,
            unit="Milliseconds",
            dimensions=dimensions,
        )

        if not success:
            self.put_metric(
                "DependencyError", 1, unit="Count", dimensions=dimensions
            )

    def record_queue_size(self, queue_name: str, size: int):
        """
        Record the size of a background queue.

        Args:
            queue_name: Name of the queue
            size: Current queue size
        """
        self.put_metric(
            "QueueSize",
            size,
            unit="Count",
            dimensions={"Queue": queue_name},
        )

    def record_health_check(self, component: str, healthy: bool):
        """
        Record health check status for a component.

        Args:
            component: Component name (e.g., 'database', 'redis')
            healthy: Whether the component is healthy
        """
        self.put_metric(
            "HealthStatus",
            1 if healthy else 0,
            unit="Count",
            dimensions={"Component": component},
        )


# Global metrics collector instance
metrics_collector = MetricsCollector()
