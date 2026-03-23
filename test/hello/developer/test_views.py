"""This file keeps tests for the developer part of the project so new changes stay safe."""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from flask import url_for

from lib.test import ViewTestMixin
from hello.developer.views import (
    _collect_resettable_fault_codes,
    _filter_incidents_after_demo_reset,
    _merge_incidents,
    _verify_fault_route,
    build_dashboard_aggregates,
    build_incident_trend,
    get_mock_incidents,
)


# This function handles the make incident work for this file.
def _make_incident(
    incident_id: str,
    opened_at: datetime,
    resolved_at: datetime | None,
    status: str,
    *,
    log_marker: str = "external_timeout",
) -> dict:
    return {
        "id": incident_id,
        "timestamp_opened": opened_at,
        "timestamp_resolved": resolved_at,
        "incident_type": "External API Degradation",
        "severity": "high",
        "status": status,
        "route": "/test-fault/external-api",
        "error_code": "FAULT_EXTERNAL_API_LATENCY",
        "symptoms": {"log_marker": log_marker},
        "verification": {"success": status == "resolved"},
        "remediation": {"execution_timestamp": None},
    }


# This class keeps the test developer incident views data and behavior in one place.
class TestDeveloperIncidentViews(ViewTestMixin):
    def test_fault_file_map_has_all_three_fault_codes(self):
        from hello.page._faulty_views_template import FAULT_FILE_MAP

        assert "FAULT_SQL_INJECTION_TEST" in FAULT_FILE_MAP
        assert "FAULT_EXTERNAL_API_LATENCY" in FAULT_FILE_MAP
        assert "FAULT_DB_TIMEOUT" in FAULT_FILE_MAP
        for info in FAULT_FILE_MAP.values():
            assert "file_path" in info
            assert "content" in info
            assert info["content"].strip()

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

    def test_merge_incidents_keeps_new_live_incident_when_cloudwatch_match_is_stale(self):
        now = datetime.now().replace(microsecond=0)
        live_incident = _make_incident(
            "LIVE-0001",
            now,
            None,
            "detected",
            log_marker="wrong_data",
        )
        cloudwatch_incident = _make_incident(
            "CW-0001",
            now - timedelta(hours=12),
            now - timedelta(hours=12),
            "resolved",
            log_marker="wrong_data",
        )

        merged = _merge_incidents([live_incident], [cloudwatch_incident])

        assert [incident["id"] for incident in merged] == ["LIVE-0001", "CW-0001"]

    def test_merge_incidents_keeps_retriggered_live_incident_separate_after_resolution(self):
        now = datetime.now().replace(microsecond=0)
        live_incident = _make_incident(
            "LIVE-0002",
            now,
            None,
            "detected",
            log_marker="external_timeout",
        )
        cloudwatch_incident = _make_incident(
            "CW-0002",
            now - timedelta(minutes=6),
            now - timedelta(minutes=1),
            "resolved",
            log_marker="external_timeout",
        )

        merged = _merge_incidents([live_incident], [cloudwatch_incident])

        assert [incident["id"] for incident in merged] == ["LIVE-0002", "CW-0002"]

    @patch("hello.developer.views.http_requests.post")
    @patch("hello.developer.views.http_requests.get")
    def test_verify_fault_route_requires_latest_build_sha(
        self,
        mock_get,
        mock_post,
        monkeypatch,
    ):
        monkeypatch.setenv("FAULT_VERIFY_BASE_URL", "https://cream.example")

        build_response = Mock()
        build_response.raise_for_status.return_value = None
        build_response.json.return_value = {"build_sha": "old-build"}
        mock_get.return_value = build_response

        ok, status, latency = _verify_fault_route(
            "FAULT_SQL_INJECTION_TEST",
            "/test-fault/run",
            expected_build_sha="new-build",
        )

        assert ok is False
        assert status == "stale_build"
        assert latency is None
        mock_post.assert_not_called()

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
        assert "External API Degradation" in aggregates["type_distribution"]["labels"]

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
        assert b"7 mins" in response.data
        assert b"88%" in response.data

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
        assert (
            b"A human-readable remediation summary was not captured for this incident."
            in response.data
        )

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
    @patch("hello.developer.views._verify_fault_route")
    @patch("hello.developer.views.get_live_incidents")
    def test_pipeline_callback_creates_resolved_incident_when_missing(
        self,
        mock_get_live_incidents,
        mock_verify_fault_route,
        mock_create_live_incident,
        mock_update_live_incident,
    ):
        mock_get_live_incidents.return_value = []
        mock_verify_fault_route.return_value = (True, "passed", 0.42)
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
        mock_verify_fault_route.assert_called_once_with(
            "FAULT_DB_TIMEOUT",
            "/test-fault/db-timeout",
            "",
        )
        assert mock_update_live_incident.call_args.args[0] == "LIVE-0002"
        assert mock_update_live_incident.call_args.args[1]["status"] == "resolved"

    @patch("hello.developer.views.update_live_incident")
    @patch("hello.developer.views._verify_fault_route")
    @patch("hello.developer.views.get_live_incidents")
    def test_pipeline_callback_updates_only_requested_fault_code(
        self,
        mock_get_live_incidents,
        mock_verify_fault_route,
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
        mock_verify_fault_route.return_value = (True, "passed", 0.18)
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
        mock_verify_fault_route.assert_called_once_with(
            "FAULT_SQL_INJECTION_TEST",
            "/test-fault/run",
            "",
        )

    @patch("hello.developer.views._clear_fault_cooldowns")
    @patch("hello.developer.views.update_live_incident")
    @patch("hello.developer.views._verify_fault_route")
    @patch("hello.developer.views.get_live_incidents")
    def test_pipeline_callback_keeps_incident_in_progress_when_route_still_fails(
        self,
        mock_get_live_incidents,
        mock_verify_fault_route,
        mock_update_live_incident,
        mock_clear_fault_cooldowns,
    ):
        mock_get_live_incidents.return_value = [
            {
                "id": "LIVE-0007",
                "error_code": "FAULT_SQL_INJECTION_TEST",
                "status": "in_progress",
                "route": "/test-fault/run",
                "symptoms": {"latency_p95_value": 0.8},
            }
        ]
        mock_verify_fault_route.return_value = (False, "http_500", 1.25)
        mock_update_live_incident.return_value = {"id": "LIVE-0007"}
        mock_clear_fault_cooldowns.return_value = {
            "cleared": ["FAULT_SQL_INJECTION_TEST"],
            "errors": {},
        }

        response = self.client.post(
            url_for("developer.pipeline_callback"),
            json={
                "fault_codes": ["FAULT_SQL_INJECTION_TEST"],
                "status": "success",
            },
        )

        assert response.status_code == 200
        assert response.get_json()["updated"] == ["LIVE-0007"]
        mock_clear_fault_cooldowns.assert_called_once_with(["FAULT_SQL_INJECTION_TEST"])
        assert mock_update_live_incident.call_args.args[0] == "LIVE-0007"
        updates = mock_update_live_incident.call_args.args[1]
        assert updates["status"] == "in_progress"
        assert updates["verification"]["success"] is False
        assert updates["verification"]["health_check_status"] == "http_500"
        assert updates["verification"]["latency_after"] == 1.25

    @patch("hello.developer.views._reset_faulty_code")
    @patch("hello.developer.views._clear_fault_cooldowns")
    @patch("hello.developer.views._record_demo_reset")
    @patch("hello.developer.views.reset_live_incidents")
    @patch("hello.developer.views.get_live_incidents")
    def test_reset_incidents_restores_only_auto_healed_faults(
        self,
        mock_get_live_incidents,
        mock_reset_live_incidents,
        mock_record_demo_reset,
        mock_clear_fault_cooldowns,
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
        mock_clear_fault_cooldowns.return_value = {
            "cleared": [
                "FAULT_DB_TIMEOUT",
                "FAULT_EXTERNAL_API_LATENCY",
                "FAULT_SQL_INJECTION_TEST",
            ],
            "errors": {},
        }
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
        assert payload["cleared_cooldowns"] == [
            "FAULT_DB_TIMEOUT",
            "FAULT_EXTERNAL_API_LATENCY",
            "FAULT_SQL_INJECTION_TEST",
        ]
        mock_clear_fault_cooldowns.assert_called_once_with([
            "FAULT_DB_TIMEOUT",
            "FAULT_EXTERNAL_API_LATENCY",
            "FAULT_SQL_INJECTION_TEST",
        ])
        mock_reset_faulty_code.assert_called_once_with([
            "FAULT_DB_TIMEOUT",
            "FAULT_EXTERNAL_API_LATENCY",
            "FAULT_SQL_INJECTION_TEST",
        ])
        mock_record_demo_reset.assert_called_once()

    @patch("hello.developer.views._fetch_incidents")
    def test_incidents_api_data_handles_missing_nested_sections(self, mock_fetch_incidents):
        now = datetime.now().replace(microsecond=0)
        mock_fetch_incidents.return_value = (
            [
                {
                    "id": "INC-NULLS",
                    "timestamp_opened": now,
                    "timestamp_resolved": None,
                    "incident_type": "External API Degradation",
                    "severity": "high",
                    "status": "detected",
                    "route": "/test-fault/external-api",
                    "error_code": "FAULT_EXTERNAL_API_LATENCY",
                    "symptoms": None,
                    "breadcrumbs": None,
                    "root_cause": None,
                    "remediation": None,
                    "verification": None,
                }
            ],
            "live",
            "CloudWatch fetch failed: boom",
        )

        response = self.client.get("/developer/incidents/api/data")

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["cloudwatch_error"] == "CloudWatch fetch failed: boom"
        assert payload["incidents"][0]["remediation"] == {}
        assert payload["incidents"][0]["verification"] == {}
        assert payload["incidents"][0]["root_cause"] == {}
        assert payload["incidents"][0]["symptoms"] == {}

    @patch("hello.developer.views.update_live_incident")
    def test_pipeline_resolve_all_is_a_noop(self, mock_update_live_incident):
        response = self.client.post(
            url_for("developer.pipeline_resolve_all"),
            json={"commit_sha": "abc123"},
        )

        assert response.status_code == 200
        assert response.get_json()["status"] == "ignored"
        mock_update_live_incident.assert_not_called()
