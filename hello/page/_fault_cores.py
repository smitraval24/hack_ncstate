"""Stable fault-route wrappers.

The self-healing loop is only allowed to edit ``hello/page/views.py``.
These routes stay registered in a separate blueprint and delegate into
that file's functions, so deploys keep a stable URL map while the fault
logic itself can still be healed and reset.
"""

from flask import Blueprint

from hello.page import views as page_views

faults = Blueprint("faults", __name__, template_folder="templates")


@faults.post("/test-fault/run")
def test_fault_run():
    return page_views.test_fault_run()


@faults.post("/test-fault/external-api")
def test_fault_external_api():
    return page_views.test_fault_external_api()


@faults.post("/test-fault/db-timeout")
def test_fault_db_timeout():
    return page_views.test_fault_db_timeout()
