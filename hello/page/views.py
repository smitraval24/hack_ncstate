import logging
import os
import time
from importlib.metadata import version

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import text

from config.settings import DEBUG
from hello.extensions import db
from hello.initializers import redis

logger = logging.getLogger(__name__)

page = Blueprint("page", __name__, template_folder="templates")


@page.get("/")
def home():
    return render_template(
        "page/home.html",
        flask_ver=version("flask"),
        python_ver=os.environ["PYTHON_VERSION"],
        debug=DEBUG,
    )


@page.get("/test/simulate")
def simulate_failure():
    """
    Test endpoint to simulate different types of failures.

    Query parameters:
    - type: slow_db, slow_api, error_500, redis_slow, timeout
    - duration: how many seconds to delay (default: 3)

    Examples:
    - /test/simulate?type=slow_db
    - /test/simulate?type=slow_api&duration=5
    - /test/simulate?type=error_500
    """
    failure_type = request.args.get("type", "slow_db")
    duration = int(request.args.get("duration", 3))

    logger.info(
        "Simulating failure",
        extra={"failure_type": failure_type, "duration": duration},
    )

    # Simulate different failure types
    if failure_type == "slow_db":
        # Simulate a slow database query
        logger.warning(
            "Simulating slow database query",
            extra={"duration_seconds": duration},
        )
        start = time.time()
        with db.engine.connect() as connection:
            # Simulate slow query with pg_sleep
            connection.execute(text(f"SELECT pg_sleep({duration})"))
        elapsed = time.time() - start

        return jsonify(
            {
                "status": "success",
                "simulation": "slow_db",
                "message": f"Database query took {elapsed:.2f}s",
                "duration": duration,
            }
        )

    elif failure_type == "slow_api":
        # Simulate a slow external API call
        logger.warning(
            "Simulating slow API call", extra={"duration_seconds": duration}
        )
        time.sleep(duration)

        return jsonify(
            {
                "status": "success",
                "simulation": "slow_api",
                "message": f"API call delayed by {duration}s",
                "duration": duration,
            }
        )

    elif failure_type == "redis_slow":
        # Simulate slow Redis operation
        logger.warning(
            "Simulating slow Redis operation",
            extra={"duration_seconds": duration},
        )
        time.sleep(duration)
        redis.ping()

        return jsonify(
            {
                "status": "success",
                "simulation": "redis_slow",
                "message": f"Redis operation took {duration}s",
                "duration": duration,
            }
        )

    elif failure_type == "error_500":
        # Simulate a 500 error
        logger.error(
            "Simulating 500 error",
            extra={"intentional": True, "test": True},
        )
        raise Exception(
            "Intentional test error - this is a simulation!"
        )

    elif failure_type == "timeout":
        # Simulate a timeout (very long operation)
        logger.error(
            "Simulating timeout",
            extra={"duration_seconds": duration},
        )
        time.sleep(duration)

        return jsonify(
            {
                "status": "success",
                "simulation": "timeout",
                "message": f"Operation took {duration}s (simulating timeout)",
                "duration": duration,
            }
        )

    else:
        return (
            jsonify(
                {
                    "error": "Unknown failure type",
                    "available_types": [
                        "slow_db",
                        "slow_api",
                        "redis_slow",
                        "error_500",
                        "timeout",
                    ],
                }
            ),
            400,
        )
