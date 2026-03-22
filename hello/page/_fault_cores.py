"""Stable fault-route wrappers.

The self-healing loop is only allowed to edit the individual fault files
(fault_sql.py, fault_api.py, fault_db.py).  These routes stay registered
in a separate blueprint and delegate into those files' functions, so deploys
keep a stable URL map while the fault logic itself can still be healed and
reset.
"""

from flask import Blueprint

from hello.page import fault_sql, fault_api, fault_db

faults = Blueprint("faults", __name__, template_folder="templates")


@faults.post("/test-fault/run")
def test_fault_run():
    return fault_sql.test_fault_run()


@faults.post("/test-fault/external-api")
def test_fault_external_api():
    return fault_api.test_fault_external_api()


@faults.post("/test-fault/db-timeout")
def test_fault_db_timeout():
    return fault_db.test_fault_db_timeout()
