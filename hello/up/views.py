import logging
import time
from datetime import datetime

from flask import Blueprint, jsonify
from sqlalchemy import text

from hello.extensions import db
from hello.initializers import redis
from hello.observability.metrics import metrics_collector

logger = logging.getLogger(__name__)

up = Blueprint("up", __name__, template_folder="templates", url_prefix="/up")


@up.get("/")
def index():
    """Simple health check - returns 200 if app is running."""
    return ""


@up.get("/databases")
def databases():
    """Legacy health check for databases - returns 200 if DB and Redis are up."""
    redis.ping()
    with db.engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return ""


@up.get("/health")
def health():
    """
    Comprehensive health check endpoint.

    Returns JSON with detailed status of all components:
    - Database (PostgreSQL)
    - Cache (Redis)
    - Celery workers (if configured)
    - Overall system health

    Returns:
        JSON response with health status
    """
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "hello-flask-app",
        "components": {},
    }

    overall_healthy = True

    # Check PostgreSQL database
    db_health = check_database()
    health_status["components"]["database"] = db_health
    if not db_health["healthy"]:
        overall_healthy = False

    # Check Redis
    redis_health = check_redis()
    health_status["components"]["redis"] = redis_health
    if not redis_health["healthy"]:
        overall_healthy = False

    # Check Celery workers (optional)
    celery_health = check_celery()
    if celery_health:
        health_status["components"]["celery"] = celery_health
        if not celery_health["healthy"]:
            overall_healthy = False

    # Set overall status
    health_status["status"] = "healthy" if overall_healthy else "unhealthy"

    # Record health metrics
    for component, status in health_status["components"].items():
        metrics_collector.record_health_check(component, status["healthy"])

    # Return appropriate HTTP status code
    status_code = 200 if overall_healthy else 503

    return jsonify(health_status), status_code


def check_database():
    """
    Check PostgreSQL database connectivity.

    Returns:
        dict: Health status of database
    """
    start_time = time.time()
    try:
        with db.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        latency_ms = (time.time() - start_time) * 1000

        metrics_collector.record_dependency_call(
            dependency="postgres", latency_ms=latency_ms, success=True
        )

        return {
            "healthy": True,
            "latency_ms": round(latency_ms, 2),
            "message": "Database connection successful",
        }
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000

        metrics_collector.record_dependency_call(
            dependency="postgres", latency_ms=latency_ms, success=False
        )

        logger.error(
            "Database health check failed", extra={"error": str(e)}
        )

        return {
            "healthy": False,
            "latency_ms": round(latency_ms, 2),
            "error": str(e),
            "message": "Database connection failed",
        }


def check_redis():
    """
    Check Redis connectivity.

    Returns:
        dict: Health status of Redis
    """
    start_time = time.time()
    try:
        redis.ping()

        latency_ms = (time.time() - start_time) * 1000

        metrics_collector.record_dependency_call(
            dependency="redis", latency_ms=latency_ms, success=True
        )

        return {
            "healthy": True,
            "latency_ms": round(latency_ms, 2),
            "message": "Redis connection successful",
        }
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000

        metrics_collector.record_dependency_call(
            dependency="redis", latency_ms=latency_ms, success=False
        )

        logger.error("Redis health check failed", extra={"error": str(e)})

        return {
            "healthy": False,
            "latency_ms": round(latency_ms, 2),
            "error": str(e),
            "message": "Redis connection failed",
        }


def check_celery():
    """
    Check Celery worker availability.

    Returns:
        dict: Health status of Celery workers, or None if not configured
    """
    try:
        from flask import current_app

        celery = current_app.extensions.get("celery")

        if not celery:
            return None

        # Check if workers are available
        inspect = celery.control.inspect()
        stats = inspect.stats()

        if stats:
            worker_count = len(stats)
            return {
                "healthy": True,
                "worker_count": worker_count,
                "message": f"{worker_count} worker(s) available",
            }
        else:
            return {
                "healthy": False,
                "worker_count": 0,
                "message": "No Celery workers available",
            }
    except Exception as e:
        logger.error(
            "Celery health check failed", extra={"error": str(e)}
        )

        return {
            "healthy": False,
            "error": str(e),
            "message": "Celery health check failed",
        }
