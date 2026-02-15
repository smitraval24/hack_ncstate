# -*- coding: utf-8 -*-

import multiprocessing
import os

from distutils.util import strtobool

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
accesslog = "-"
errorlog = "-"
access_log_format = (
    "%(h)s %(l)s %(u)s %(t)s '%(r)s' %(s)s %(b)s '%(f)s' '%(a)s' in %(D)sµs"  # noqa: E501
)

# Capture stdout/stderr from app workers and send it to Gunicorn's errorlog.
# With errorlog='-' this ends up on container stderr and is picked up by ECS.
capture_output = True

# Default log level (can be overridden by env var).
loglevel = os.getenv("LOG_LEVEL", "info").lower()

# Use threaded workers so SSE streams don't block all capacity.
worker_class = os.getenv("WEB_WORKER_CLASS", "gthread")
workers = int(os.getenv("WEB_CONCURRENCY", multiprocessing.cpu_count() * 2))
threads = int(os.getenv("PYTHON_MAX_THREADS", 4))

reload = bool(strtobool(os.getenv("WEB_RELOAD", "false")))

timeout = int(os.getenv("WEB_TIMEOUT", 120))
