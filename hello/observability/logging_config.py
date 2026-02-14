"""Structured logging configuration for the application."""

import logging
import os
import sys

from pythonjsonlogger import jsonlogger


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter with additional context."""

    def add_fields(self, log_record, record, message_dict):
        """Add custom fields to log records."""
        super().add_fields(log_record, record, message_dict)

        # Add standard fields
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["timestamp"] = self.formatTime(record, self.datefmt)

        # Add environment context
        log_record["environment"] = os.getenv("FLASK_ENV", "production")
        log_record["service"] = "hello-flask-app"


def setup_logging(app=None):
    """
    Configure structured JSON logging for the application.

    Args:
        app: Flask application instance (optional)

    Returns:
        Logger instance
    """
    log_level = os.getenv("LOG_LEVEL", "INFO")

    # Create logger
    logger = logging.getLogger()
    logger.setLevel(log_level)

    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Console handler with JSON formatting
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # JSON formatter
    formatter = CustomJsonFormatter(
        fmt="%(timestamp)s %(level)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)

    # Optionally add CloudWatch handler in production
    if os.getenv("ENABLE_CLOUDWATCH_LOGS", "false").lower() == "true":
        try:
            import watchtower

            cloudwatch_handler = watchtower.CloudWatchLogHandler(
                log_group=os.getenv("CLOUDWATCH_LOG_GROUP", "/hello/app"),
                stream_name=os.getenv(
                    "CLOUDWATCH_STREAM_NAME", "flask-app"
                ),
            )
            cloudwatch_handler.setFormatter(formatter)
            logger.addHandler(cloudwatch_handler)

            if app:
                app.logger.info(
                    "CloudWatch logging enabled",
                    extra={
                        "log_group": os.getenv(
                            "CLOUDWATCH_LOG_GROUP", "/hello/app"
                        )
                    },
                )
        except ImportError:
            if app:
                app.logger.warning(
                    "CloudWatch logging requested but watchtower not available"
                )

    # Configure Flask app logger
    if app:
        app.logger.handlers = logger.handlers
        app.logger.setLevel(log_level)
        app.logger.info(
            "Structured logging initialized",
            extra={"log_level": log_level, "format": "json"},
        )

    return logger


def get_logger(name):
    """
    Get a logger instance with the specified name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
