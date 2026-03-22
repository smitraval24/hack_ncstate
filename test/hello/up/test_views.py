"""This file keeps tests for the up part of the project so new changes stay safe."""

from flask import url_for

from lib.test import ViewTestMixin


# This class keeps the test up data and behavior in one place.
class TestUp(ViewTestMixin):
    def test_up(self):
        """Up should respond with a success 200."""
        response = self.client.get(url_for("up.index"))

        assert response.status_code == 200

    def test_up_databases(self):
        """Up databases should respond with a success 200."""
        response = self.client.get(url_for("up.databases"))

        assert response.status_code == 200

    def test_up_build(self, monkeypatch):
        """Up build should expose the running build SHA."""
        monkeypatch.setenv("BUILD_SHA", "abcdef123456")

        response = self.client.get(url_for("up.build"))

        assert response.status_code == 200
        assert response.get_json() == {
            "build_sha": "abcdef123456",
            "build_short_sha": "abcdef1",
        }
