"""This file keeps tests for the page part of the project so new changes stay safe."""

from unittest.mock import Mock

import requests
from flask import url_for

from lib.test import ViewTestMixin


# This class keeps the test page data and behavior in one place.
class TestPage(ViewTestMixin):
    def test_home_page(self):
        """Home page should respond with a success 200."""
        response = self.client.get(url_for("page.home"))

        assert response.status_code == 200
        assert b"Autonomous Recovery System" in response.data
        assert b"From fault signal to production fix, automatically." in response.data

    def test_test_fault_page_matches_landing_style_direction(self):
        response = self.client.get(url_for("page.test_fault"))

        assert response.status_code == 200
        assert b"Fault Injection Test Page" in response.data
        assert b"Demo Control" in response.data
        assert b"External API Latency Simulation" in response.data
        assert b"Build local" in response.data
        assert b"data-fault-form" in response.data

    def test_sql_fault_route_wrapper_delegates_to_page_views(self, monkeypatch):
        wrapped = Mock(return_value=("wrapped-response", 200))
        monkeypatch.setattr("hello.page._fault_cores.page_views.test_fault_run", wrapped)

        response = self.client.post("/test-fault/run")

        assert response.status_code == 200
        assert response.data == b"wrapped-response"
        wrapped.assert_called_once_with()

    def test_test_fault_run_returns_fault_signal(self, monkeypatch):
        execute = Mock(side_effect=RuntimeError("bad sql"))
        rollback = Mock()
        create_live_incident = Mock()

        monkeypatch.setattr("hello.page.views.db.session.execute", execute)
        monkeypatch.setattr("hello.page.views.db.session.rollback", rollback)
        monkeypatch.setattr("hello.page.views.create_live_incident", create_live_incident)

        response = self.client.post("/test-fault/run")

        assert response.status_code == 500
        assert b"FAULT_SQL_INJECTION_TEST" in response.data
        execute.assert_called_once()
        rollback.assert_called_once()
        create_live_incident.assert_called_once_with(
            error_code="FAULT_SQL_INJECTION_TEST",
            route="/test-fault/run",
            reason="invalid_sql_executed",
        )

    def test_test_fault_external_api_returns_timeout_fault_signal(self, monkeypatch):
        create_live_incident = Mock()
        monkeypatch.setattr(
            "hello.page.views.requests.get",
            Mock(side_effect=requests.exceptions.Timeout("timed out")),
        )
        monkeypatch.setattr("hello.page.views.create_live_incident", create_live_incident)

        response = self.client.post("/test-fault/external-api")

        assert response.status_code == 504
        assert b"FAULT_EXTERNAL_API_LATENCY" in response.data
        create_live_incident.assert_called_once()
        assert create_live_incident.call_args.kwargs["reason"] == "external_timeout"

    def test_test_fault_external_api_returns_wrong_data_fault_signal(self, monkeypatch):
        response_obj = Mock()
        response_obj.raise_for_status = Mock()
        response_obj.json = Mock(return_value={"value": "forty-two"})
        create_live_incident = Mock()

        monkeypatch.setattr("hello.page.views.requests.get", Mock(return_value=response_obj))
        monkeypatch.setattr("hello.page.views.create_live_incident", create_live_incident)

        response = self.client.post("/test-fault/external-api")

        assert response.status_code == 504
        assert b"FAULT_EXTERNAL_API_LATENCY" in response.data
        assert b"wrong_data" in response.data
        create_live_incident.assert_called_once()
        assert create_live_incident.call_args.kwargs["reason"] == "wrong_data"

    def test_test_fault_db_timeout_returns_fault_signal(self, monkeypatch):
        execute = Mock(side_effect=[None, RuntimeError("statement timeout")])
        rollback = Mock()
        create_live_incident = Mock()

        monkeypatch.setattr("hello.page.views.db.session.execute", execute)
        monkeypatch.setattr("hello.page.views.db.session.rollback", rollback)
        monkeypatch.setattr("hello.page.views.create_live_incident", create_live_incident)

        response = self.client.post("/test-fault/db-timeout")

        assert response.status_code == 500
        assert b"FAULT_DB_TIMEOUT" in response.data
        assert execute.call_count == 2
        rollback.assert_called_once()
        create_live_incident.assert_called_once()
        assert create_live_incident.call_args.kwargs["reason"] == "db_statement_timeout"
