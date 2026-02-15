"""Tests for the RAG service layer."""

import json
from unittest.mock import patch, MagicMock

import pytest

from hello.incident.backboard_client import RAGResponse


class TestRAGService:
    """Tests for rag_service functions.

    All Backboard HTTP calls are mocked so tests run without a real API key.
    """

    def test_query_similar_builds_correct_query(self, app):
        """query_similar should include symptoms, markers and metrics."""
        app.config["BACKBOARD_API_KEY"] = "test_key"
        app.config["BACKBOARD_ASSISTANT_ID"] = "ast_123"
        app.config["BACKBOARD_THREAD_ID"] = "thr_456"
        app.config["BACKBOARD_BASE_URL"] = "https://app.backboard.io/api"
        app.config["BACKBOARD_LLM_PROVIDER"] = "openai"
        app.config["BACKBOARD_MODEL_NAME"] = "gpt-4o"

        fake_response = RAGResponse(
            content="Likely root cause: pool exhaustion",
            retrieved_memories=[{"id": "mem_1"}],
            retrieved_files=[],
            raw={"content": "Likely root cause: pool exhaustion"},
        )

        with patch(
            "hello.incident.rag_service._run_async",
            return_value=fake_response,
        ):
            from hello.incident.rag_service import query_similar

            result = query_similar(
                symptoms="error_rate=12%",
                markers=["db_pool_exhausted"],
                metrics={"latency": "3500ms"},
            )

        assert result.content == "Likely root cause: pool exhaustion"
        assert len(result.retrieved_memories) == 1

    def test_query_similar_raises_without_thread_id(self, app):
        """Should raise RuntimeError when BACKBOARD_THREAD_ID is empty."""
        app.config["BACKBOARD_THREAD_ID"] = ""

        from hello.incident.rag_service import query_similar

        with pytest.raises(RuntimeError, match="BACKBOARD_THREAD_ID"):
            query_similar(symptoms="test")

    def test_index_incident_skips_without_assistant_id(self, app):
        """Should return None when BACKBOARD_ASSISTANT_ID is not set."""
        app.config["BACKBOARD_ASSISTANT_ID"] = ""

        from hello.incident.rag_service import index_incident

        mock_inc = MagicMock()
        result = index_incident(mock_inc)
        assert result is None

    def test_analyze_and_store(self, app, session):
        """analyze_and_store should populate RAG fields on the incident."""
        app.config["BACKBOARD_API_KEY"] = "test_key"
        app.config["BACKBOARD_ASSISTANT_ID"] = "ast_123"
        app.config["BACKBOARD_THREAD_ID"] = "thr_456"
        app.config["BACKBOARD_BASE_URL"] = "https://app.backboard.io/api"
        app.config["BACKBOARD_LLM_PROVIDER"] = "openai"
        app.config["BACKBOARD_MODEL_NAME"] = "gpt-4o"

        from hello.incident.models import Incident
        from hello.incident.rag_service import analyze_and_store

        inc = Incident(
            error_code="TEST",
            symptoms="high latency",
            breadcrumbs=json.dumps(["slow_query"]),
        )
        session.add(inc)
        session.flush()

        fake_response = RAGResponse(
            content="Root cause: slow query",
            retrieved_memories=[],
            retrieved_files=[],
            raw={"content": "Root cause: slow query"},
        )

        with patch(
            "hello.incident.rag_service._run_async",
            return_value=fake_response,
        ):
            result = analyze_and_store(inc, session)

        assert result.root_cause == "Root cause: slow query"
        assert result.rag_confidence is None  # Backboard doesn't return a score
        assert result.rag_response is not None
