"""Thin wrapper around the Backboard.io REST API.

Uses ``httpx`` for async HTTP so that the RAG pipeline can be awaited from
both sync Flask views (via ``asyncio.run``) and Celery workers.

API Reference: https://docs.backboard.io/

Key operations
--------------
* ``create_assistant``  – provision a RAG assistant on Backboard
* ``create_thread``     – create a conversation thread under an assistant
* ``upload_document``   – upload a file to an assistant's RAG store
* ``add_message``       – send a message and get an LLM + RAG response
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lightweight response containers
# ---------------------------------------------------------------------------

@dataclass
class AssistantInfo:
    assistant_id: str
    name: str
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class ThreadInfo:
    thread_id: str
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class DocumentInfo:
    document_id: str
    filename: str = ""
    status: str = "pending"
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class RAGResponse:
    content: str
    retrieved_memories: list[dict] = field(default_factory=list)
    retrieved_files: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class BackboardClient:
    """Async context-manager client for Backboard.io.

    Base URL: ``https://app.backboard.io/api``
    Auth: ``X-API-Key`` header.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://app.backboard.io/api",
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # -- context manager -----------------------------------------------------

    async def __aenter__(self) -> "BackboardClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "X-API-Key": self._api_key,
            },
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- helpers -------------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "BackboardClient must be used as an async context manager"
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict:
        client = self._ensure_client()
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    # -- public API ----------------------------------------------------------

    async def create_assistant(
        self,
        name: str,
        system_prompt: str,
    ) -> AssistantInfo:
        """Create a new Backboard RAG assistant.

        POST /assistants
        """
        data = await self._request(
            "POST",
            "/assistants",
            json={
                "name": name,
                "system_prompt": system_prompt,
            },
        )
        aid = data.get("assistant_id", "")
        logger.info("Created Backboard assistant: %s", aid)
        return AssistantInfo(
            assistant_id=aid,
            name=data.get("name", name),
            raw=data,
        )

    async def create_thread(
        self,
        assistant_id: str,
    ) -> ThreadInfo:
        """Create a conversation thread under an assistant.

        POST /assistants/{assistant_id}/threads
        """
        data = await self._request(
            "POST",
            f"/assistants/{assistant_id}/threads",
            json={},
        )
        tid = data.get("thread_id", "")
        logger.info(
            "Created thread %s for assistant %s", tid, assistant_id
        )
        return ThreadInfo(thread_id=tid, raw=data)

    async def upload_document(
        self,
        assistant_id: str,
        content: str,
        filename: str = "incident.txt",
    ) -> DocumentInfo:
        """Upload a text document to an assistant's RAG store.

        POST /assistants/{assistant_id}/documents  (multipart file upload)

        Backboard expects a file upload. We create an in-memory text file
        from the ``content`` string.
        """
        file_obj = io.BytesIO(content.encode("utf-8"))
        client = self._ensure_client()
        response = await client.post(
            f"/assistants/{assistant_id}/documents",
            files={"file": (filename, file_obj, "text/plain")},
        )
        response.raise_for_status()
        data = response.json()

        doc_id = data.get("document_id", "")
        logger.info(
            "Uploaded document %s for assistant %s", doc_id, assistant_id
        )
        return DocumentInfo(
            document_id=doc_id,
            filename=data.get("filename", filename),
            status=data.get("status", "pending"),
            raw=data,
        )

    async def add_message(
        self,
        thread_id: str,
        content: str,
        llm_provider: str = "openai",
        model_name: str = "gpt-4o",
        memory: str = "Auto",
        stream: bool = False,
    ) -> RAGResponse:
        """Send a message to a thread and get the LLM + RAG response.

        POST /threads/{thread_id}/messages  (multipart form data)

        Backboard automatically performs vector retrieval from indexed
        documents and memories, augments the prompt, and returns the
        LLM answer together with ``retrieved_memories`` and
        ``retrieved_files``.
        """
        form_data = {
            "content": content,
            "llm_provider": llm_provider,
            "model_name": model_name,
            "memory": memory,
            "stream": str(stream).lower(),
        }
        client = self._ensure_client()
        response = await client.post(
            f"/threads/{thread_id}/messages",
            data=form_data,
        )
        response.raise_for_status()
        data = response.json()

        answer = data.get("content", data.get("message", ""))
        memories = data.get("retrieved_memories", [])
        files = data.get("retrieved_files", [])

        return RAGResponse(
            content=answer,
            retrieved_memories=memories,
            retrieved_files=files,
            raw=data,
        )

    async def get_document_status(
        self,
        document_id: str,
    ) -> dict:
        """Check the processing status of a document.

        GET /documents/{document_id}/status
        """
        return await self._request(
            "GET", f"/documents/{document_id}/status"
        )

    async def list_documents(
        self,
        assistant_id: str,
    ) -> list[dict]:
        """List documents indexed under an assistant.

        GET /assistants/{assistant_id}/documents
        """
        data = await self._request(
            "GET",
            f"/assistants/{assistant_id}/documents",
        )
        return data if isinstance(data, list) else []

    async def delete_document(
        self,
        document_id: str,
    ) -> dict:
        """Delete a single document.

        DELETE /documents/{document_id}
        """
        return await self._request("DELETE", f"/documents/{document_id}")
