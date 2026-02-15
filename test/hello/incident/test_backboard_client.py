"""Tests for the Backboard client wrapper."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from hello.incident.backboard_client import (
    BackboardClient,
    AssistantInfo,
    DocumentInfo,
    RAGResponse,
    ThreadInfo,
)


def test_create_assistant():
    """create_assistant should POST to /assistants and return AssistantInfo."""

    async def _run():
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "assistant_id": "ast_123",
            "name": "Test Assistant",
        }
        mock_response.raise_for_status = lambda: None

        with patch(
            "httpx.AsyncClient.request", return_value=mock_response
        ):
            async with BackboardClient(api_key="test_key") as client:
                info = await client.create_assistant(
                    name="Test Assistant",
                    system_prompt="You are helpful.",
                )
                assert isinstance(info, AssistantInfo)
                assert info.assistant_id == "ast_123"

    asyncio.run(_run())


def test_create_thread():
    """create_thread should POST to /assistants/{id}/threads."""

    async def _run():
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "thread_id": "thr_456",
            "created_at": "2026-02-14T00:00:00",
        }
        mock_response.raise_for_status = lambda: None

        with patch(
            "httpx.AsyncClient.request", return_value=mock_response
        ):
            async with BackboardClient(api_key="test_key") as client:
                info = await client.create_thread("ast_123")
                assert isinstance(info, ThreadInfo)
                assert info.thread_id == "thr_456"

    asyncio.run(_run())


def test_upload_document():
    """upload_document should upload a file to the assistant."""

    async def _run():
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "document_id": "doc_789",
            "filename": "incident.txt",
            "status": "pending",
        }
        mock_response.raise_for_status = lambda: None

        with patch(
            "httpx.AsyncClient.post", return_value=mock_response
        ):
            async with BackboardClient(api_key="test_key") as client:
                info = await client.upload_document(
                    assistant_id="ast_123",
                    content="Some incident content",
                )
                assert isinstance(info, DocumentInfo)
                assert info.document_id == "doc_789"

    asyncio.run(_run())


def test_add_message():
    """add_message should POST form data to /threads/{id}/messages."""

    async def _run():
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": "Root cause is likely X. Remediate by Y.",
            "message": "ok",
            "thread_id": "thr_456",
            "retrieved_memories": [
                {"id": "mem_1", "memory": "past incident", "score": 0.92},
            ],
            "retrieved_files": ["doc_789"],
        }
        mock_response.raise_for_status = lambda: None

        with patch(
            "httpx.AsyncClient.post", return_value=mock_response
        ):
            async with BackboardClient(api_key="test_key") as client:
                resp = await client.add_message(
                    thread_id="thr_456",
                    content="Symptoms: high error rate",
                )
                assert isinstance(resp, RAGResponse)
                assert "Root cause" in resp.content
                assert len(resp.retrieved_memories) == 1
                assert resp.retrieved_files == ["doc_789"]

    asyncio.run(_run())


def test_client_requires_context_manager():
    """Calling methods without context manager should raise RuntimeError."""

    async def _run():
        client = BackboardClient(api_key="test_key")
        with pytest.raises(RuntimeError, match="context manager"):
            await client.create_assistant("X", "Y")

    asyncio.run(_run())
