"""Seed the Backboard RAG knowledge base with resolved incident examples.

Uploads incident documents so that the RAG pipeline has historical context
to draw from during live analysis.

Usage
-----
From Flask CLI::

    flask seed-kb

Or programmatically::

    from hello.incident.seed_knowledge_base import seed_knowledge_base
    seed_knowledge_base()
"""

from __future__ import annotations

import asyncio
import logging
import time

from flask import current_app

from hello.incident.backboard_client import BackboardClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Knowledge-base entries
# ---------------------------------------------------------------------------

KB_ENTRIES: list[dict] = [
    # --- Fault descriptions ---
    {
        "filename": "kb_fault_sql.txt",
        "content": (
            "ErrorCode: FAULT_SQL_INJECTION_TEST\n"
            "File: hello/page/views_sql.py\n"
            "Function: test_fault_run\n"
            "Fault: Line `db.session.execute(text('SELECT FROM'))` executes "
            "malformed SQL — missing column list and table name. PostgreSQL "
            "rejects it with 'syntax error at or near FROM'.\n"
            "Solution: Change `text('SELECT FROM')` to `text('SELECT 1')`.\n"
        ),
    },
    {
        "filename": "kb_fault_api.txt",
        "content": (
            "ErrorCode: FAULT_EXTERNAL_API_LATENCY\n"
            "File: hello/page/views_api.py\n"
            "Function: test_fault_external_api\n"
            "Fault: Line `requests.get(f'{mock_api_base_url}/data', timeout=3)` "
            "uses a 3-second timeout against a mock API that can take up to 8 "
            "seconds, causing requests.exceptions.Timeout.\n"
            "Solution: Change `timeout=3` to `timeout=10`.\n"
        ),
    },
    {
        "filename": "kb_fault_db.txt",
        "content": (
            "ErrorCode: FAULT_DB_TIMEOUT\n"
            "File: hello/page/views_db.py\n"
            "Function: test_fault_db_timeout\n"
            "Fault: Line `db.session.execute(text('SELECT pg_sleep(10);'))` "
            "sleeps for 10 seconds but statement_timeout is set to 5500ms, "
            "so PostgreSQL always cancels the query with a timeout error.\n"
            "Solution: Change `pg_sleep(10)` to `pg_sleep(1)`.\n"
        ),
    },
    # --- Verified working fixes from self-healing loop ---
    {
        "filename": "kb_resolved_sql.txt",
        "content": (
            "ErrorCode: FAULT_SQL_INJECTION_TEST\n"
            "File: hello/page/views_sql.py\n"
            "Function: test_fault_run\n"
            "Status: RESOLVED — verified working fix from self-healing loop\n"
            "RootCause: `db.session.execute(text('SELECT FROM'))` is malformed "
            "SQL that PostgreSQL rejects with a syntax error.\n"
            "Remediation: Changed exactly one line in `hello/page/views_sql.py`: "
            "`text('SELECT FROM')` became `text('SELECT 1')`. No other file was "
            "modified, and `hello/page/views.py` stayed untouched.\n"
            "KeyChanges:\n"
            "  - `db.session.execute(text('SELECT FROM'))` → "
            "`db.session.execute(text('SELECT 1'))`\n"
            "  - No import changes\n"
            "  - No routing or shared-file changes\n"
        ),
    },
    {
        "filename": "kb_resolved_api.txt",
        "content": (
            "ErrorCode: FAULT_EXTERNAL_API_LATENCY\n"
            "File: hello/page/views_api.py\n"
            "Function: test_fault_external_api\n"
            "Status: RESOLVED — verified working fix from self-healing loop\n"
            "RootCause: The external API call used a 3-second timeout against a "
            "mock API that can take up to 8 seconds, causing "
            "requests.exceptions.Timeout regularly.\n"
            "Remediation: Changed exactly one line in `hello/page/views_api.py`: "
            "`timeout=3` became `timeout=10`. No retry loop, no new imports, and "
            "no changes to `hello/page/views.py`.\n"
            "KeyChanges:\n"
            "  - `requests.get(..., timeout=3)` → `requests.get(..., timeout=10)`\n"
            "  - Exception handlers stayed unchanged\n"
            "  - No routing or shared-file changes\n"
        ),
    },
    {
        "filename": "kb_resolved_db.txt",
        "content": (
            "ErrorCode: FAULT_DB_TIMEOUT\n"
            "File: hello/page/views_db.py\n"
            "Function: test_fault_db_timeout\n"
            "Status: RESOLVED — verified working fix from self-healing loop\n"
            "RootCause: `statement_timeout` was set to 5500ms but `pg_sleep(10)` "
            "requires 10 seconds, so PostgreSQL always cancelled the query.\n"
            "Remediation: Changed exactly one line in `hello/page/views_db.py`: "
            "`pg_sleep(10)` became `pg_sleep(1)`. No timeout rewrite, no extra "
            "commits, and no changes to `hello/page/views.py`.\n"
            "KeyChanges:\n"
            "  - `db.session.execute(text('SELECT pg_sleep(10);'))` → "
            "`db.session.execute(text('SELECT pg_sleep(1);'))`\n"
            "  - `statement_timeout` stayed unchanged\n"
            "  - No routing or shared-file changes\n"
        ),
    },
]


# ---------------------------------------------------------------------------
# Seeder logic
# ---------------------------------------------------------------------------

# This function seeds the knowledge base work used in this file.
def seed_knowledge_base() -> list[dict]:
    """Upload all KB entries to the Backboard assistant's document store.

    Returns a list of dicts with ``filename`` and ``document_id`` for each
    successfully uploaded entry.
    """
    api_key = current_app.config.get("BACKBOARD_API_KEY", "")
    base_url = current_app.config.get("BACKBOARD_BASE_URL", "")
    assistant_id = current_app.config.get("BACKBOARD_ASSISTANT_ID", "")

    if not api_key or not assistant_id:
        raise RuntimeError(
            "BACKBOARD_API_KEY and BACKBOARD_ASSISTANT_ID must be set. "
            "Run 'flask setup-assistant' first."
        )

    async def _upload_all() -> list[dict]:
        results = []
        async with BackboardClient(api_key=api_key, base_url=base_url) as client:
            for i, entry in enumerate(KB_ENTRIES, 1):
                try:
                    doc = await client.upload_document(
                        assistant_id=assistant_id,
                        content=entry["content"],
                        filename=entry["filename"],
                    )
                    results.append({
                        "filename": entry["filename"],
                        "document_id": doc.document_id,
                        "status": doc.status,
                    })
                    logger.info(
                        "[%d/%d] Uploaded %s → doc_id=%s",
                        i, len(KB_ENTRIES), entry["filename"], doc.document_id,
                    )
                except Exception as exc:
                    logger.exception(
                        "[%d/%d] Failed to upload %s",
                        i, len(KB_ENTRIES), entry["filename"],
                    )
                    results.append({
                        "filename": entry["filename"],
                        "document_id": None,
                        "error": str(exc),
                    })
                # Delay to avoid rate-limiting
                await asyncio.sleep(1.5)
        return results

    return asyncio.run(_upload_all())


def clear_knowledge_base() -> list[dict]:
    """Delete all documents from the Backboard assistant's document store.

    Returns a list of dicts with ``document_id`` and ``deleted`` status.
    """
    api_key = current_app.config.get("BACKBOARD_API_KEY", "")
    base_url = current_app.config.get("BACKBOARD_BASE_URL", "")
    assistant_id = current_app.config.get("BACKBOARD_ASSISTANT_ID", "")

    if not api_key or not assistant_id:
        raise RuntimeError(
            "BACKBOARD_API_KEY and BACKBOARD_ASSISTANT_ID must be set. "
            "Run 'flask setup-assistant' first."
        )

    async def _delete_all() -> list[dict]:
        results = []
        async with BackboardClient(api_key=api_key, base_url=base_url) as client:
            docs = await client.list_documents(assistant_id)
            for i, doc in enumerate(docs, 1):
                doc_id = doc.get("document_id", doc.get("id", ""))
                try:
                    await client.delete_document(doc_id)
                    results.append({"document_id": doc_id, "deleted": True})
                    logger.info("[%d/%d] Deleted doc_id=%s", i, len(docs), doc_id)
                except Exception as exc:
                    logger.exception("[%d/%d] Failed to delete doc_id=%s", i, len(docs), doc_id)
                    results.append({"document_id": doc_id, "deleted": False, "error": str(exc)})
                await asyncio.sleep(0.5)
        return results

    return asyncio.run(_delete_all())
