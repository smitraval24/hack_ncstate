"""Tests for Incident API views."""

import json
from unittest.mock import patch, MagicMock

from lib.test import ViewTestMixin
from hello.incident.models import Incident


class TestIncidentViews(ViewTestMixin):
    """Tests for the /incidents/ blueprint."""

    def test_list_incidents_empty(self):
        response = self.client.get("/incidents/")
        assert response.status_code == 200
        assert response.get_json() == []

    def test_get_incident_not_found(self):
        response = self.client.get("/incidents/9999")
        assert response.status_code == 404

    @patch("hello.incident.views.record_incident")
    def test_create_incident(self, mock_record):
        """POST /incidents/ should record an incident and return 201."""
        mock_incident = MagicMock(spec=Incident)
        mock_incident.to_dict.return_value = {
            "id": 1,
            "error_code": "TEST_ERR",
            "symptoms": "test symptoms",
            "resolved": False,
            "detected_at": "2026-02-14T00:00:00",
            "updated_at": "2026-02-14T00:00:00",
            "breadcrumbs": "[]",
            "root_cause": None,
            "remediation": None,
            "verification": None,
            "rag_query": None,
            "rag_response": None,
            "rag_confidence": None,
            "backboard_doc_id": None,
        }
        mock_record.return_value = mock_incident

        response = self.client.post(
            "/incidents/",
            data=json.dumps({
                "error_code": "TEST_ERR",
                "symptoms": "test symptoms",
                "breadcrumbs": ["marker1"],
            }),
            content_type="application/json",
        )

        assert response.status_code == 201
        data = response.get_json()
        assert data["error_code"] == "TEST_ERR"
        mock_record.assert_called_once()

    def test_dashboard_renders(self):
        response = self.client.get("/incidents/dashboard")
        assert response.status_code == 200
        assert b"Incident Dashboard" in response.data

    def test_agent_plan_returns_playbook_for_known_fault(self):
        incident = Incident(
            error_code="FAULT_EXTERNAL_API_LATENCY",
            symptoms="external timeout",
            breadcrumbs=json.dumps(["external_api_call", "requests_timeout"]),
            rag_response=json.dumps(
                {
                    "content": "timeout from upstream, add retry and fallback",
                    "retrieved_memories": [{"id": "mem_1"}],
                    "retrieved_files": ["kb_api_latency_001.txt"],
                }
            ),
        )
        self.session.add(incident)
        self.session.flush()

        response = self.client.post(f"/incidents/{incident.id}/agent-plan")
        assert response.status_code == 200

        payload = response.get_json()
        assert payload["decision"] == "ready_for_approval"
        assert (
            payload["selected_action"]["action_id"]
            == "fix_fault_external_api_latency"
        )

    def test_agent_execute_requires_approval(self):
        incident = Incident(
            error_code="FAULT_SQL_INJECTION_TEST",
            symptoms="syntax error",
            rag_response=json.dumps({"content": "invalid sql rollback needed"}),
        )
        self.session.add(incident)
        self.session.flush()

        response = self.client.post(
            f"/incidents/{incident.id}/agent-execute",
            data=json.dumps({"approve": False}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.get_json()["status"] == "approval_required"

    def test_agent_execute_returns_pipeline_payload_on_approval(self):
        incident = Incident(
            error_code="FAULT_DB_TIMEOUT",
            symptoms="db timeout",
            rag_response=json.dumps(
                {
                    "content": "queuepool limit reached during pg_sleep",
                    "retrieved_memories": [{"id": "mem_1"}],
                }
            ),
        )
        self.session.add(incident)
        self.session.flush()

        response = self.client.post(
            f"/incidents/{incident.id}/agent-execute",
            data=json.dumps({"approve": True}),
            content_type="application/json",
        )
        assert response.status_code == 200

        payload = response.get_json()
        assert payload["status"] == "approved_for_pipeline"
        assert payload["execution"]["action_id"] == "fix_fault_db_timeout"
