"""This file keeps tests for the developer part of the project so new changes stay safe."""

from datetime import datetime, timedelta
from unittest.mock import patch

from flask import url_for

from lib.test import ViewTestMixin
from hello.developer.views import build_incident_trend, get_mock_incidents


# This function handles the make incident work for this file.
def _make_incident(
    incident_id: str,
    opened_at: datetime,
    resolved_at: datetime | None,
    status: str,
) -> dict:
    return {
        "id": incident_id,
        "timestamp_opened": opened_at,
        "timestamp_resolved": resolved_at,
        "incident_type": "External API Timeout",
        "severity": "high",
        "status": status,
        "route": "/test-fault/external-api",
        "error_code": "FAULT_EXTERNAL_API_LATENCY",
        "verification": {"success": status == "resolved"},
        "remediation": {"execution_timestamp": None},
    }


# This class keeps the test developer incident views data and behavior in one place.
class TestDeveloperIncidentViews(ViewTestMixin):
    def test_build_incident_trend_aggregates_last_seven_days(self):
        now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        incidents = [
            _make_incident("INC-1", now, now, "resolved"),
            _make_incident("INC-2", now - timedelta(days=2), None, "detected"),
            _make_incident("INC-3", now - timedelta(days=2, hours=1), now - timedelta(days=1), "resolved"),
            _make_incident("INC-4", now - timedelta(days=8), now - timedelta(days=8), "resolved"),
        ]

        trend = build_incident_trend(incidents)

        assert len(trend["labels"]) == 7
        assert trend["detected"][-1] == 1
        assert trend["resolved"][-1] == 1
        assert trend["detected"][-3] == 2
        assert trend["resolved"][-2] == 1
        assert sum(trend["detected"]) == 3
        assert sum(trend["resolved"]) == 2

    @patch("hello.developer.views._fetch_incidents")
    def test_incidents_api_data_includes_real_trend_data(self, mock_fetch_incidents):
        now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        incidents = [
            _make_incident("INC-1", now, now, "resolved"),
            _make_incident("INC-2", now - timedelta(days=1), None, "detected"),
            _make_incident("INC-3", now - timedelta(days=1, hours=2), now, "resolved"),
        ]
        mock_fetch_incidents.return_value = (incidents, "live", None)

        response = self.client.get("/developer/incidents/api/data")

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["data_source"] == "live"
        assert payload["trend_data"]["detected"][-1] == 1
        assert payload["trend_data"]["detected"][-2] == 2
        assert payload["trend_data"]["resolved"][-1] == 2
        assert len(payload["trend_data"]["labels"]) == 7

    @patch("hello.developer.views._fetch_incidents")
    def test_incidents_dashboard_renders_control_room_theme(self, mock_fetch_incidents):
        now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        incidents = [
            _make_incident("INC-1", now, now, "resolved"),
            _make_incident("INC-2", now - timedelta(hours=1), None, "detected"),
        ]
        mock_fetch_incidents.return_value = (incidents, "live", None)

        response = self.client.get(url_for("developer.incidents_dashboard"))

        assert response.status_code == 200
        assert b"Incident Center" in response.data
        assert b"autonomous recovery pipeline" in response.data

    @patch("hello.developer.views._fetch_incidents")
    def test_incident_detail_renders_dark_report_theme(self, mock_fetch_incidents):
        incident = get_mock_incidents()[0]
        mock_fetch_incidents.return_value = ([incident], "mock", None)

        response = self.client.get(
            url_for("developer.incident_detail", incident_id=incident["id"])
        )

        assert response.status_code == 200
        assert incident["id"].encode() in response.data
        assert b"Incident Report" in response.data
        assert b"Root Cause Analysis" in response.data
