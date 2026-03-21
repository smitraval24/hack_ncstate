"""This file keeps tests for the page part of the project so new changes stay safe."""

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

    def test_test_fault_run_returns_sql_fault_signal(self, monkeypatch):
        execute = Mock(side_effect=RuntimeError("syntax error at or near FROM"))
        rollback = Mock()

        monkeypatch.setattr("hello.page.views.db.session.execute", execute)
        monkeypatch.setattr("hello.page.views.db.session.rollback", rollback)

        response = self.client.post(url_for("page.test_fault_run"))

        assert response.status_code == 500
        assert b"FAULT_SQL_INJECTION_TEST" in response.data
        execute.assert_called_once()
        rollback.assert_called_once()
