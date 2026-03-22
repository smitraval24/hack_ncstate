"""This file keeps tests for the developer part of the project so new changes stay safe."""

from datetime import datetime, timedelta
from unittest.mock import patch

from flask import url_for

from lib.test import ViewTestMixin
from hello.developer.views import (
    _collect_resettable_fault_codes,
    _fault_codes_differing_from_template,
    _filter_incidents_after_demo_reset,
    _restore_faulty_functions,
    build_dashboard_aggregates,
    build_incident_trend,
    get_mock_incidents,
)
from hello.page._faulty_views_template import FAULTY_VIEWS_CONTENT


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
    def test_restore_faulty_functions_only_reverts_selected_fault_handlers(self):
        current_source = (
            FAULTY_VIEWS_CONTENT
            .replace('db.session.execute(text("SELECT FROM"))', 'db.session.execute(text("SELECT 1"))', 1)
            .replace(
                'requests.get(f"{mock_api_base_url}/data", timeout=3)',
                'requests.get(f"{mock_api_base_url}/data", timeout=10)',
                1,
            )
        )

        restored = _restore_faulty_functions(
            current_source,
            ["FAULT_SQL_INJECTION_TEST"],
        )

        assert 'db.session.execute(text("SELECT FROM"))' in restored
        assert 'requests.get(f"{mock_api_base_url}/data", timeout=10)' in restored
        assert restored.count('db.session.execute(text("SELECT FROM"))') == 1

    def test_fault_codes_differing_from_template_detects_healed_handlers(self):
        current_source = (
            FAULTY_VIEWS_CONTENT
            .replace('db.session.execute(text("SELECT FROM"))', 'db.session.execute(text("SELECT 1"))', 1)
            .replace(
                "db.session.execute(text(\"SET LOCAL statement_timeout = \\'2s\\';\"))",
                "db.session.execute(text(\"SET LOCAL statement_timeout = \\'10s\\';\"))",
                1,
            )
        )

        assert _fault_codes_differing_from_template(current_source) == [
            "FAULT_DB_TIMEOUT",
            "FAULT_SQL_INJECTION_TEST",
        ]

    def test_collect_resettable_fault_codes_only_includes_auto_healed_resolved_faults(self):
        incidents = [
            {
                "error_code": "FAULT_SQL_INJECTION_TEST",
                "status": "resolved",
                "remediation": {"action_type": "auto_fix_pushed"},
                "verification": {"success": True},
            },
            {
                "error_code": "FAULT_EXTERNAL_API_LATENCY",
                "status": "resolved",
                "remediation": {"action_type": None},
                "verification": {"success": True},
            },
            {
                "error_code": "FAULT_DB_TIMEOUT",
                "status": "in_progress",
                "remediation": {"action_type": "auto_fix_pushed"},
                "verification": {"success": None},
            },
        ]

        assert _collect_resettable_fault_codes(incidents) == [
            "FAULT_SQL_INJECTION_TEST"
        ]

    @patch("hello.developer.views._get_demo_reset_timestamp")
    def test_filter_incidents_after_demo_reset_removes_stale_items(self, mock_cutoff):
        cutoff = datetime.now().replace(microsecond=0)
        mock_cutoff.return_value = cutoff
        incidents = [
            {"id": "before", "timestamp_opened": cutoff - timedelta(minutes=5)},
            {"id": "after", "timestamp_opened": cutoff + timedelta(minutes=5)},
            {"id": "unknown", "timestamp_opened": None},
        ]

        filtered = _filter_incidents_after_demo_reset(incidents)

        assert [incident["id"] for incident in filtered] == ["after", "unknown"]

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
        assert payload["dashboard_aggregates"]["impacted_requests_total"] == 0
        assert payload["dashboard_aggregates"]["severity_counts"]["high"] == 3

    def test_build_dashboard_aggregates_counts_existing_incident_data(self):
        incidents = get_mock_incidents()

        aggregates = build_dashboard_aggregates(incidents)

        assert aggregates["impacted_requests_total"] == 321
        assert aggregates["severity_counts"]["critical"] == 2
        assert aggregates["severity_counts"]["high"] == 2
        assert aggregates["severity_counts"]["medium"] == 1
        assert "/test-fault/external-api" in aggregates["route_impact"]["labels"]
        assert "External API Timeout" in aggregates["type_distribution"]["labels"]

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
        assert b"Detected vs Resolved" in response.data
        assert b"Severity Distribution" in response.data
        assert b"Incident Feed" in response.data

    @patch("hello.developer.views._fetch_incidents")
    def test_incident_detail_renders_dark_report_theme(self, mock_fetch_incidents):
        incident = get_mock_incidents()[0]
        mock_fetch_incidents.return_value = ([incident], "mock", None)

        response = self.client.get(
            url_for("developer.incident_detail", incident_id=incident["id"])
        )

        assert response.status_code == 200
        assert incident["id"].encode() in response.data
        assert b"What broke" in response.data
        assert b"Logs and evidence" in response.data
        assert b"How it was fixed" in response.data

    @patch("hello.developer.views._fetch_incidents")
    def test_incident_detail_shows_fallbacks_when_optional_fields_missing(self, mock_fetch_incidents):
        incident = get_mock_incidents()[0]
        incident["root_cause"] = {"source": None, "confidence_score": None, "explanation": None}
        incident["verification"] = {
            "error_rate_before": None,
            "error_rate_after": None,
            "latency_before": None,
            "latency_after": None,
            "health_check_status": None,
            "success": None,
        }
        incident["status"] = "detected"
        incident["timestamp_resolved"] = None
        incident["remediation"] = {
            "action_type": None,
            "parameters": None,
            "execution_timestamp": None,
        }
        mock_fetch_incidents.return_value = ([incident], "mock", None)

        response = self.client.get(
            url_for("developer.incident_detail", incident_id=incident["id"])
        )

        assert response.status_code == 200
        assert b"Root-cause analysis is still pending for this incident." in response.data
        assert b"Not available for this incident." in response.data

    @patch("hello.developer.views.update_live_incident")
    @patch("hello.developer.views.create_live_incident")
    @patch("hello.developer.views.get_live_incidents")
    def test_pipeline_pending_creates_live_incident_when_missing(
        self,
        mock_get_live_incidents,
        mock_create_live_incident,
        mock_update_live_incident,
    ):
        mock_get_live_incidents.return_value = []
        mock_create_live_incident.return_value = {"id": "LIVE-0001"}
        mock_update_live_incident.return_value = {"id": "LIVE-0001"}

        response = self.client.post(
            url_for("developer.pipeline_pending"),
            json={
                "fault_code": "FAULT_SQL_INJECTION_TEST",
                "route": "/test-fault/run",
                "reason": "invalid_sql_executed",
                "rag_analysis": "Malformed SQL detected",
                "claude_output": "Applied parameterized query fix",
            },
        )

        assert response.status_code == 200
        assert response.get_json()["updated"] == ["LIVE-0001"]
        mock_create_live_incident.assert_called_once_with(
            error_code="FAULT_SQL_INJECTION_TEST",
            route="/test-fault/run",
            reason="invalid_sql_executed",
        )

    @patch("hello.developer.views.update_live_incident")
    @patch("hello.developer.views.create_live_incident")
    @patch("hello.developer.views.get_live_incidents")
    def test_pipeline_callback_creates_resolved_incident_when_missing(
        self,
        mock_get_live_incidents,
        mock_create_live_incident,
        mock_update_live_incident,
    ):
        mock_get_live_incidents.return_value = []
        mock_create_live_incident.return_value = {
            "id": "LIVE-0002",
            "symptoms": {"error_rate_value": 1, "latency_p95_value": 5.0},
        }
        mock_update_live_incident.return_value = {"id": "LIVE-0002"}

        response = self.client.post(
            url_for("developer.pipeline_callback"),
            json={
                "fault_codes": ["FAULT_DB_TIMEOUT"],
                "status": "success",
            },
        )

        assert response.status_code == 200
        assert response.get_json()["updated"] == ["LIVE-0002"]
        mock_create_live_incident.assert_called_once_with(
            error_code="FAULT_DB_TIMEOUT",
            route="/test-fault/db-timeout",
            reason="pipeline_success",
        )
        assert mock_update_live_incident.call_args.args[0] == "LIVE-0002"
        assert mock_update_live_incident.call_args.args[1]["status"] == "resolved"

    @patch("hello.developer.views.update_live_incident")
    @patch("hello.developer.views.get_live_incidents")
    def test_pipeline_callback_updates_only_requested_fault_code(
        self,
        mock_get_live_incidents,
        mock_update_live_incident,
    ):
        mock_get_live_incidents.return_value = [
            {
                "id": "LIVE-0001",
                "error_code": "FAULT_SQL_INJECTION_TEST",
                "status": "in_progress",
                "symptoms": {"latency_p95_value": 0},
            },
            {
                "id": "LIVE-0002",
                "error_code": "FAULT_DB_TIMEOUT",
                "status": "detected",
                "symptoms": {"latency_p95_value": 5},
            },
        ]
        mock_update_live_incident.return_value = {"id": "LIVE-0001"}

        response = self.client.post(
            url_for("developer.pipeline_callback"),
            json={
                "fault_codes": ["FAULT_SQL_INJECTION_TEST"],
                "status": "success",
            },
        )

        assert response.status_code == 200
        assert response.get_json()["updated"] == ["LIVE-0001"]
        assert mock_update_live_incident.call_count == 1
        assert mock_update_live_incident.call_args.args[0] == "LIVE-0001"

    @patch("hello.developer.views._reset_faulty_code")
    @patch("hello.developer.views._pause_self_healing")
    @patch("hello.developer.views._record_demo_reset")
    @patch("hello.developer.views.reset_live_incidents")
    @patch("hello.developer.views.get_live_incidents")
    def test_reset_incidents_restores_only_auto_healed_faults(
        self,
        mock_get_live_incidents,
        mock_reset_live_incidents,
        mock_record_demo_reset,
        mock_pause_self_healing,
        mock_reset_faulty_code,
    ):
        mock_get_live_incidents.return_value = [
            {
                "error_code": "FAULT_SQL_INJECTION_TEST",
                "status": "resolved",
                "remediation": {"action_type": "auto_fix_pushed"},
                "verification": {"success": True},
            },
            {
                "error_code": "FAULT_DB_TIMEOUT",
                "status": "detected",
                "remediation": {"action_type": None},
                "verification": {"success": None},
            },
        ]
        mock_reset_live_incidents.return_value = 2
        mock_reset_faulty_code.return_value = {
            "success": True,
            "fault_codes": ["FAULT_SQL_INJECTION_TEST", "FAULT_DB_TIMEOUT"],
        }

        response = self.client.post(url_for("developer.reset_incidents"))

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["restored_fault_codes"] == [
            "FAULT_SQL_INJECTION_TEST",
            "FAULT_DB_TIMEOUT",
        ]
        mock_reset_faulty_code.assert_called_once_with(["FAULT_SQL_INJECTION_TEST"])
        mock_pause_self_healing.assert_called_once()
        mock_record_demo_reset.assert_called_once()

    @patch("hello.developer.views.update_live_incident")
    def test_pipeline_resolve_all_is_a_noop(self, mock_update_live_incident):
        response = self.client.post(
            url_for("developer.pipeline_resolve_all"),
            json={"commit_sha": "abc123"},
        )

        assert response.status_code == 200
        assert response.get_json()["status"] == "ignored"
        mock_update_live_incident.assert_not_called()
