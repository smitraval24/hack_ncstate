"""This file keeps tests for the page part of the project so new changes stay safe."""

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
