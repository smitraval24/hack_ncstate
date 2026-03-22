"""This file keeps tests for the page part of the project so new changes stay safe."""

import requests
from unittest.mock import Mock

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

    def test_test_fault_run_succeeds_after_sql_fix(self, monkeypatch):
        execute = Mock()
        resolve_live_incidents = Mock()

        monkeypatch.setattr("hello.page.views.db.session.execute", execute)
        monkeypatch.setattr("hello.page.views._resolve_live_incidents", resolve_live_incidents)

        response = self.client.post(url_for("page.test_fault_run"))

        assert response.status_code == 200
        execute.assert_called_once()
        resolve_live_incidents.assert_called_once_with(
            "FAULT_SQL_INJECTION_TEST",
            "/test-fault/run",
        )

    def test_test_fault_external_api_returns_timeout_fault_signal(self, monkeypatch):
        monkeypatch.setattr(
            "hello.page.views.requests.get",
            Mock(side_effect=requests.exceptions.Timeout("timed out")),
        )

        response = self.client.post(url_for("page.test_fault_external_api"))

        assert response.status_code == 504
        assert b"FAULT_EXTERNAL_API_LATENCY" in response.data

    def test_test_fault_db_timeout_returns_fault_signal(self, monkeypatch):
        execute = Mock(side_effect=[None, RuntimeError("statement timeout")])
        rollback = Mock()

        monkeypatch.setattr("hello.page.views.db.session.execute", execute)
        monkeypatch.setattr("hello.page.views.db.session.rollback", rollback)

        response = self.client.post(url_for("page.test_fault_db_timeout"))

        assert response.status_code == 500
        assert b"FAULT_DB_TIMEOUT" in response.data
        assert execute.call_count == 2
        rollback.assert_called_once()
