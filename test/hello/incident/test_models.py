"""Tests for Incident model."""

import json

from hello.incident.models import Incident


class TestIncidentModel:
    def test_to_dict_returns_all_fields(self, app, session):
        inc = Incident(
            error_code="TEST_ERROR",
            symptoms="high latency",
            breadcrumbs=json.dumps(["marker_a"]),
        )
        session.add(inc)
        session.flush()

        d = inc.to_dict()
        assert d["error_code"] == "TEST_ERROR"
        assert d["symptoms"] == "high latency"
        assert d["resolved"] is False
        assert "detected_at" in d
        assert "updated_at" in d

    def test_to_document_content(self, app, session):
        inc = Incident(
            error_code="DB_TIMEOUT",
            symptoms="pool exhausted",
            root_cause="too many connections",
            remediation="restart pool",
        )
        session.add(inc)
        session.flush()

        content = inc.to_document_content()
        assert "DB_TIMEOUT" in content
        assert "pool exhausted" in content
        assert "too many connections" in content
        assert "restart pool" in content

    def test_repr(self, app, session):
        inc = Incident(error_code="X", symptoms="y")
        session.add(inc)
        session.flush()
        assert "X" in repr(inc)
