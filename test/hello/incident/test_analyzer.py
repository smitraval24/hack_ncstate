"""Tests for the incident analyzer module."""

import json
from unittest.mock import patch, MagicMock

from lib.test import ViewTestMixin
from hello.incident.models import Incident


class TestAnalyzer(ViewTestMixin):
    """Tests for analyzer.record_incident and detect_and_analyze."""

    def test_record_incident_creates_record(self):
        """record_incident should persist an Incident row."""
        from hello.incident.analyzer import record_incident

        inc = record_incident(
            error_code="FAULT_TEST",
            symptoms="test symptom",
            breadcrumbs=["breadcrumb_1"],
        )
        assert inc.id is not None
        assert inc.error_code == "FAULT_TEST"
        assert inc.symptoms == "test symptom"
        assert json.loads(inc.breadcrumbs) == ["breadcrumb_1"]
        assert inc.resolved is False

    @patch("hello.incident.analyzer.analyze_incident")
    def test_detect_and_analyze_calls_rag(self, mock_analyze):
        """detect_and_analyze should record + run analysis."""
        from hello.incident.analyzer import detect_and_analyze

        mock_analyze.side_effect = lambda inc: inc

        inc = detect_and_analyze(
            error_code="E1",
            symptoms="timeout",
            breadcrumbs=["external_timeout"],
            metrics={"latency": "4s"},
        )

        assert inc.error_code == "E1"
        mock_analyze.assert_called_once()

    @patch("hello.incident.analyzer.analyze_incident")
    def test_detect_and_analyze_continues_on_rag_failure(
        self, mock_analyze
    ):
        """If RAG fails, incident should still be returned."""
        from hello.incident.analyzer import detect_and_analyze

        mock_analyze.side_effect = RuntimeError("Backboard unreachable")

        inc = detect_and_analyze(
            error_code="E2",
            symptoms="connection_refused",
        )

        assert inc.id is not None
        assert inc.error_code == "E2"

    def test_resolve_incident(self):
        """resolve_incident should update fields and commit."""
        from hello.incident.analyzer import record_incident, resolve_incident

        inc = record_incident(
            error_code="RES_TEST",
            symptoms="test",
        )

        with patch(
            "hello.incident.analyzer.index_incident", return_value="doc_99"
        ):
            resolved = resolve_incident(
                inc,
                root_cause="root",
                remediation="fix",
                verification="verified",
            )

        assert resolved.resolved is True
        assert resolved.root_cause == "root"
        assert resolved.remediation == "fix"
        assert resolved.backboard_doc_id == "doc_99"
