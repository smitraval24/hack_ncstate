"""RAG service – orchestrates Backboard queries for the self-healing pipeline.

This module is the single integration point between the Flask application and
the Backboard.io RAG engine.  It exposes synchronous helpers (safe to call from
regular Flask views / Celery tasks) that internally run the async Backboard
client via ``asyncio.run``.

API docs: https://docs.backboard.io/

Typical flow
------------
1. ``setup_assistant()``           – one-time: create assistant + thread
2. ``index_incident(incident)``    – embed a resolved incident for future use
3. ``query_similar(symptoms, …)``  – retrieve similar past incidents + LLM
                                     suggestions during a live incident
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from flask import current_app

from hello.incident.backboard_client import (
    AssistantInfo,
    BackboardClient,
    RAGResponse,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_config(key: str) -> str:
    """Read a Backboard-related config value from the Flask app config."""
    return current_app.config.get(key, "")


def _make_client() -> BackboardClient:
    return BackboardClient(
        api_key=_get_config("BACKBOARD_API_KEY"),
        base_url=_get_config("BACKBOARD_BASE_URL"),
    )


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from synchronous Flask code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_assistant(
    name: str = "Incident RAG Assistant",
    system_prompt: str | None = None,
) -> dict:
    """Create a Backboard assistant **and** an initial thread.

    Returns a dict with ``assistant_id`` and ``thread_id``.
    Both should be persisted in ``.env`` as ``BACKBOARD_ASSISTANT_ID``
    and ``BACKBOARD_THREAD_ID`` respectively.
    """
    if system_prompt is None:
        system_prompt = (
            "You are an incident analysis assistant.  Use the documents "
            "stored for past incident diagnosis and remediation to "
            "suggest root cause analysis and safe remediation actions."
        )

    async def _inner() -> dict:
        async with _make_client() as client:
            assistant = await client.create_assistant(
                name=name,
                system_prompt=system_prompt,
            )
            thread = await client.create_thread(assistant.assistant_id)
            return {
                "assistant_id": assistant.assistant_id,
                "thread_id": thread.thread_id,
                "assistant_raw": assistant.raw,
                "thread_raw": thread.raw,
            }

    result = _run_async(_inner())
    logger.info(
        "Backboard assistant created: assistant_id=%s thread_id=%s",
        result["assistant_id"],
        result["thread_id"],
    )
    return result


def index_incident(incident: Any) -> str | None:
    """Index a resolved incident into Backboard for future RAG retrieval.

    Uploads the incident as a text file to the assistant's document store.
    Returns the Backboard document ID on success, or ``None`` on failure.
    """
    assistant_id = _get_config("BACKBOARD_ASSISTANT_ID")
    if not assistant_id:
        logger.warning(
            "BACKBOARD_ASSISTANT_ID not configured – skipping index"
        )
        return None

    content = incident.to_document_content()

    async def _inner() -> str:
        async with _make_client() as client:
            doc = await client.upload_document(
                assistant_id=assistant_id,
                content=content,
                filename=f"incident_{incident.id}.txt",
            )
            return doc.document_id

    try:
        doc_id = _run_async(_inner())
        logger.info(
            "Indexed incident %s as Backboard doc %s",
            incident.id,
            doc_id,
        )
        return doc_id
    except Exception:
        logger.exception("Failed to index incident %s", incident.id)
        return None


def query_similar(
    symptoms: str,
    markers: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> RAGResponse:
    """Query Backboard RAG for similar past incidents.

    Constructs a natural-language query from the provided symptoms, log
    markers, and metric snapshot, sends it through the Backboard retrieval
    pipeline, and returns the response containing retrieved memories/files
    and the LLM-generated answer.
    """
    thread_id = _get_config("BACKBOARD_THREAD_ID")
    if not thread_id:
        raise RuntimeError(
            "BACKBOARD_THREAD_ID is not configured.  "
            "Run setup_assistant() first and save both "
            "BACKBOARD_ASSISTANT_ID and BACKBOARD_THREAD_ID."
        )

    # Build the query text --------------------------------------------------
    parts = [f"New incident detected:\nSymptoms: {symptoms}"]
    if markers:
        parts.append(f"Markers: {', '.join(markers)}")
    if metrics:
        metric_str = ", ".join(f"{k}={v}" for k, v in metrics.items())
        parts.append(f"Metrics: {metric_str}")
    parts.append(
        "What are the closest past incidents and recommended remediations?"
    )
    query_text = "\n".join(parts)

    llm_provider = _get_config("BACKBOARD_LLM_PROVIDER") or "openai"
    model_name = _get_config("BACKBOARD_MODEL_NAME") or "gpt-4o"

    async def _inner() -> RAGResponse:
        async with _make_client() as client:
            return await client.add_message(
                thread_id=thread_id,
                content=query_text,
                llm_provider=llm_provider,
                model_name=model_name,
                memory="Auto",
                stream=False,
            )

    response = _run_async(_inner())
    logger.info(
        "RAG query returned %d memories, %d files",
        len(response.retrieved_memories or []),
        len(response.retrieved_files or []),
    )
    return response


def analyze_and_store(
    incident: Any,
    db_session: Any,
) -> Any:
    """End-to-end RAG analysis: query → store results on the incident.

    This is the primary entry-point used by views and Celery tasks.
    """
    markers: list[str] = []
    try:
        markers = json.loads(incident.breadcrumbs)
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        response = query_similar(
            symptoms=incident.symptoms,
            markers=markers,
        )
    except Exception:
        logger.exception("RAG query failed for incident %s", incident.id)
        return incident

    incident.rag_query = json.dumps({
        "symptoms": incident.symptoms,
        "markers": markers,
    })
    incident.rag_response = json.dumps({
        "content": response.content,
        "retrieved_memories": response.retrieved_memories or [],
        "retrieved_files": response.retrieved_files or [],
    })
    incident.rag_confidence = None  # Backboard doesn't return a score

    # Use the LLM-generated content as the root-cause suggestion when none
    # has been manually provided yet.
    if not incident.root_cause and response.content:
        incident.root_cause = response.content

    db_session.add(incident)
    db_session.commit()

    return incident
