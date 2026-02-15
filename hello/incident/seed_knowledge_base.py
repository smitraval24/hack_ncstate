"""Seed the Backboard RAG knowledge base with resolved incident examples.

Uploads 15 pre-written incident documents (5 per error category) so that
the RAG pipeline has historical context to draw from during live analysis.

Error categories
----------------
* ``FAULT_SQL_INJECTION_TEST``   – SQL syntax / injection errors
* ``FAULT_DB_TIMEOUT``           – database timeout / pool exhaustion
* ``FAULT_EXTERNAL_API_LATENCY`` – external API latency / failures

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
# Knowledge-base entries – 5 per error type
# ---------------------------------------------------------------------------

KB_ENTRIES: list[dict] = [
    # -----------------------------------------------------------------------
    # FAULT_SQL_INJECTION_TEST  (SQL errors)
    # -----------------------------------------------------------------------
    {
        "filename": "kb_sql_error_001.txt",
        "content": (
            "IncidentID: KB-SQL-001\n"
            "ErrorCode: FAULT_SQL_INJECTION_TEST\n"
            "Symptoms: ProgrammingError raised on /test-fault/run – "
            "malformed SQL 'SELECT FROM' executed against PostgreSQL. "
            "Error: syntax error at or near 'FROM'.\n"
            "Breadcrumbs: [\"invalid_sql_executed\", \"test_fault_endpoint\"]\n"
            "RootCause: The test-fault endpoint deliberately executes "
            "invalid SQL ('SELECT FROM') without a column list or table. "
            "PostgreSQL rejects the statement with a syntax error.\n"
            "Remediation: In production, validate and parameterise all SQL "
            "statements. For the test endpoint, catch the exception, log it, "
            "and return a controlled 500 response. Roll back the database "
            "session to avoid a stuck transaction.\n"
            "Verification: Re-ran the endpoint; exception is caught, session "
            "rolled back, and a structured JSON error is returned.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_sql_error_002.txt",
        "content": (
            "IncidentID: KB-SQL-002\n"
            "ErrorCode: FAULT_SQL_INJECTION_TEST\n"
            "Symptoms: Unhandled sqlalchemy.exc.ProgrammingError caused a "
            "500 Internal Server Error. Gunicorn worker logged "
            "'FAULT_SQL_INJECTION_TEST route=/test-fault/run "
            "reason=invalid_sql_executed'. Sentry captured the traceback.\n"
            "Breadcrumbs: [\"invalid_sql_executed\", \"db_session_rollback_needed\"]\n"
            "RootCause: Raw SQL string passed to db.session.execute() "
            "without parameterisation. The statement 'SELECT FROM' is "
            "syntactically invalid in every SQL dialect.\n"
            "Remediation: Wrapped the execute call in a try/except block, "
            "issued db.session.rollback(), and returned a JSON error payload "
            "with HTTP 500. Added a unit test to assert the rollback.\n"
            "Verification: Automated test suite passes; no open transactions "
            "after the fault endpoint fires.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_sql_error_003.txt",
        "content": (
            "IncidentID: KB-SQL-003\n"
            "ErrorCode: FAULT_SQL_INJECTION_TEST\n"
            "Symptoms: Repeated ProgrammingError exceptions saturated the "
            "Gunicorn error log. Database connection pool showed 'idle in "
            "transaction' sessions because the failed statement was not "
            "rolled back.\n"
            "Breadcrumbs: [\"invalid_sql_executed\", \"pool_saturation\", "
            "\"idle_in_transaction\"]\n"
            "RootCause: Each invocation of the fault endpoint opened a "
            "transaction that was never rolled back after the exception, "
            "leaving connections in 'idle in transaction' state. After "
            "several requests the pool was exhausted.\n"
            "Remediation: Added explicit db.session.rollback() in the "
            "except handler. Configured SQLAlchemy pool_pre_ping=True and "
            "pool_recycle=300 to recover stale connections.\n"
            "Verification: Ran 50 sequential fault requests; pool utilisation "
            "remained stable and no 'idle in transaction' sessions appeared "
            "in pg_stat_activity.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_sql_error_004.txt",
        "content": (
            "IncidentID: KB-SQL-004\n"
            "ErrorCode: FAULT_SQL_INJECTION_TEST\n"
            "Symptoms: Application returned HTTP 500 with an HTML traceback "
            "page instead of JSON. The traceback contained the raw SQL "
            "statement, potentially leaking schema information.\n"
            "Breadcrumbs: [\"invalid_sql_executed\", \"traceback_leaked\", "
            "\"html_error_page\"]\n"
            "RootCause: DEBUG mode was enabled in production, causing Flask "
            "to render the interactive debugger with the full traceback "
            "including the SQL statement.\n"
            "Remediation: Disabled DEBUG in production. Added a custom "
            "error handler for 500 that returns a generic JSON response "
            "a generic JSON error body and logs the real exception "
            "to Sentry.\n"
            "Verification: Triggered the fault; response is now a safe "
            "JSON body with no traceback or SQL leakage.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_sql_error_005.txt",
        "content": (
            "IncidentID: KB-SQL-005\n"
            "ErrorCode: FAULT_SQL_INJECTION_TEST\n"
            "Symptoms: Monitoring alert fired for elevated 5xx rate on "
            "/test-fault/run. CloudWatch logs show 12 occurrences of "
            "'FAULT_SQL_INJECTION_TEST reason=invalid_sql_executed' within "
            "60 seconds.\n"
            "Breadcrumbs: [\"invalid_sql_executed\", \"5xx_spike\", "
            "\"cloudwatch_alarm\"]\n"
            "RootCause: A load-test script targeted the fault endpoint "
            "without rate limiting, producing a burst of invalid SQL "
            "statements and triggering the monitoring alarm.\n"
            "Remediation: Applied rate limiting (10 req/min) to the "
            "/test-fault/* endpoints using Flask-Limiter. Added an "
            "allow-list so only internal IPs can trigger faults.\n"
            "Verification: Re-ran the load test; requests beyond the limit "
            "receive HTTP 429 and the alarm did not fire.\n"
            "Resolved: True\n"
        ),
    },

    # -----------------------------------------------------------------------
    # FAULT_DB_TIMEOUT  (database timeout / pool exhaustion)
    # -----------------------------------------------------------------------
    {
        "filename": "kb_db_timeout_001.txt",
        "content": (
            "IncidentID: KB-DBT-001\n"
            "ErrorCode: FAULT_DB_TIMEOUT\n"
            "Symptoms: POST /test-fault/db-timeout returned HTTP 500 after "
            "~5 seconds. Logs show 'FAULT_DB_TIMEOUT "
            "route=/test-fault/db-timeout "
            "reason=db_timeout_or_pool_exhaustion latency=5.02'.\n"
            "Breadcrumbs: [\"pg_sleep_executed\", \"statement_timeout\", "
            "\"db_timeout\"]\n"
            "RootCause: The endpoint executes 'SELECT pg_sleep(5)' which "
            "blocks the database connection for 5 seconds. When the "
            "statement_timeout (3 s) fires, PostgreSQL cancels the query "
            "and SQLAlchemy raises OperationalError.\n"
            "Remediation: Set statement_timeout=3000 at the session level "
            "so long-running queries are cancelled. Caught the exception, "
            "rolled back the session, and returned a structured error.\n"
            "Verification: Endpoint now returns HTTP 504 with "
            "{'error_code': 'FAULT_DB_TIMEOUT', 'detail': 'statement "
            "timeout'} within 3 seconds.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_db_timeout_002.txt",
        "content": (
            "IncidentID: KB-DBT-002\n"
            "ErrorCode: FAULT_DB_TIMEOUT\n"
            "Symptoms: Multiple Gunicorn workers hung simultaneously. "
            "Health-check endpoint /up returned 503. pg_stat_activity "
            "showed 8 sessions in 'active' state all running pg_sleep.\n"
            "Breadcrumbs: [\"pg_sleep_executed\", \"worker_hang\", "
            "\"pool_exhaustion\", \"health_check_fail\"]\n"
            "RootCause: Concurrent requests to /test-fault/db-timeout "
            "consumed all connections in the SQLAlchemy pool (pool_size=5). "
            "Subsequent requests, including the health check, could not "
            "acquire a connection and timed out.\n"
            "Remediation: Reduced pg_sleep duration to 1 s for testing. "
            "Increased pool_size to 10 and set pool_timeout=5. Added a "
            "dedicated connection for the health-check endpoint that "
            "bypasses the shared pool.\n"
            "Verification: Ran 20 concurrent fault requests; health check "
            "remained responsive throughout.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_db_timeout_003.txt",
        "content": (
            "IncidentID: KB-DBT-003\n"
            "ErrorCode: FAULT_DB_TIMEOUT\n"
            "Symptoms: Periodic 500 errors on unrelated endpoints after "
            "triggering db-timeout fault. Error: 'QueuePool limit of 5 "
            "reached, connection timed out'. Latency on /incidents/ jumped "
            "from 50 ms to 8 s.\n"
            "Breadcrumbs: [\"pg_sleep_executed\", \"pool_overflow\", "
            "\"queue_pool_limit\", \"cross_endpoint_impact\"]\n"
            "RootCause: pg_sleep held connections for 5 s each, exhausting "
            "the pool. Other endpoints waited in the queue until "
            "pool_timeout expired, then raised TimeoutError.\n"
            "Remediation: Set SQLALCHEMY_POOL_SIZE=10, "
            "SQLALCHEMY_MAX_OVERFLOW=5, and SQLALCHEMY_POOL_TIMEOUT=10. "
            "Added a circuit breaker that aborts long-running test queries "
            "after 2 s.\n"
            "Verification: Pool metrics show max 7 active connections "
            "during load; no spillover to other endpoints.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_db_timeout_004.txt",
        "content": (
            "IncidentID: KB-DBT-004\n"
            "ErrorCode: FAULT_DB_TIMEOUT\n"
            "Symptoms: AWS RDS CPU utilisation spiked to 95 % when the "
            "db-timeout fault was triggered repeatedly. CloudWatch alarm "
            "'rds-high-cpu' fired. Read replicas showed replication lag.\n"
            "Breadcrumbs: [\"pg_sleep_executed\", \"rds_cpu_spike\", "
            "\"replication_lag\", \"cloudwatch_alarm\"]\n"
            "RootCause: Each pg_sleep call holds an active backend process "
            "on the RDS instance. Under rapid repeated invocation the "
            "instance's CPU was consumed scheduling and maintaining those "
            "backends.\n"
            "Remediation: Moved the fault endpoint behind a feature flag "
            "that is off by default (ENABLE_FAULT_INJECTION=false). Added "
            "server-side rate limiting (5 req/min). Configured RDS "
            "statement_timeout=3000.\n"
            "Verification: CPU stayed below 30 % during controlled fault "
            "testing with rate limits in place.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_db_timeout_005.txt",
        "content": (
            "IncidentID: KB-DBT-005\n"
            "ErrorCode: FAULT_DB_TIMEOUT\n"
            "Symptoms: Application startup failed with 'could not connect "
            "to server: Connection timed out'. DB host was reachable but "
            "pg_hba.conf rejected connections from the new pod IP range.\n"
            "Breadcrumbs: [\"db_connection_timeout\", \"startup_failure\", "
            "\"pg_hba_reject\"]\n"
            "RootCause: Kubernetes cluster scaled into a new subnet whose "
            "CIDR was not listed in pg_hba.conf, causing PostgreSQL to "
            "reject TCP connections from those IPs.\n"
            "Remediation: Updated pg_hba.conf to include the full VPC CIDR "
            "range. Configured connection health checks at startup "
            "(pool_pre_ping=True) so the app fails fast with a clear error "
            "instead of hanging.\n"
            "Verification: Pods in the new subnet connect successfully; "
            "startup health check passes.\n"
            "Resolved: True\n"
        ),
    },

    # -----------------------------------------------------------------------
    # FAULT_EXTERNAL_API_LATENCY  (external API timeouts / failures)
    # -----------------------------------------------------------------------
    {
        "filename": "kb_api_latency_001.txt",
        "content": (
            "IncidentID: KB-API-001\n"
            "ErrorCode: FAULT_EXTERNAL_API_LATENCY\n"
            "Symptoms: POST /test-fault/external-api returned HTTP 504 "
            "with detail 'timeout'. Logs: 'FAULT_EXTERNAL_API_LATENCY "
            "route=/test-fault/external-api reason=external_timeout "
            "latency=3.01'.\n"
            "Breadcrumbs: [\"external_api_call\", \"requests_timeout\", "
            "\"mock_api_slow\"]\n"
            "RootCause: The mock API (mock_api:5001) was configured with "
            "API_FAULT_MODE=latency, causing it to sleep 2-8 s on 60 % of "
            "requests. With a 3 s client timeout, roughly half the calls "
            "timed out.\n"
            "Remediation: Implemented exponential back-off retry (max 3 "
            "attempts, base 0.5 s). Added a circuit breaker that opens "
            "after 5 consecutive failures and returns a cached fallback "
            "response.\n"
            "Verification: Under the same fault mode, the endpoint returns "
            "a valid response within 6 s in 98 % of cases.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_api_latency_002.txt",
        "content": (
            "IncidentID: KB-API-002\n"
            "ErrorCode: FAULT_EXTERNAL_API_LATENCY\n"
            "Symptoms: External API returned HTTP 500 upstream failure. "
            "Logs: 'FAULT_EXTERNAL_API_LATENCY "
            "route=/test-fault/external-api reason=upstream_failure "
            "latency=0.45'. Error rate on the endpoint reached 30 %.\n"
            "Breadcrumbs: [\"external_api_call\", \"upstream_500\", "
            "\"error_rate_spike\"]\n"
            "RootCause: The mock API's error mode (API_FAULT_MODE=error) "
            "returns HTTP 500 on 30 % of requests, simulating an unstable "
            "upstream service.\n"
            "Remediation: Added retry-on-5xx logic with jittered back-off. "
            "Wrapped the call in a try/except for HTTPError and returned a "
            "degraded but valid response with a warning header "
            "'X-Upstream-Degraded: true' to signal degradation.\n"
            "Verification: Error rate dropped to < 2 % with retries "
            "enabled.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_api_latency_003.txt",
        "content": (
            "IncidentID: KB-API-003\n"
            "ErrorCode: FAULT_EXTERNAL_API_LATENCY\n"
            "Symptoms: ConnectionError when calling mock_api:5001/data. "
            "Logs: 'FAULT_EXTERNAL_API_LATENCY "
            "route=/test-fault/external-api reason=connection_error "
            "latency=0.01'. The mock_api container had been stopped.\n"
            "Breadcrumbs: [\"external_api_call\", \"connection_refused\", "
            "\"container_down\"]\n"
            "RootCause: The mock_api Docker service was not running "
            "(docker-compose profile excluded it). The requests library "
            "raised ConnectionError immediately.\n"
            "Remediation: Added a health-check dependency in compose.yaml "
            "so the web service waits for mock_api to be healthy before "
            "accepting traffic. Added a fallback path that returns a "
            "cached/default value when the upstream is unreachable.\n"
            "Verification: Stopped mock_api; the endpoint returns "
            "a JSON object with value=null and fallback=true instead of crashing.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_api_latency_004.txt",
        "content": (
            "IncidentID: KB-API-004\n"
            "ErrorCode: FAULT_EXTERNAL_API_LATENCY\n"
            "Symptoms: p99 latency on /test-fault/external-api degraded "
            "to 7.8 s. Downstream users reported slow page loads. "
            "Gunicorn workers saturated waiting on external calls.\n"
            "Breadcrumbs: [\"external_api_call\", \"high_latency\", "
            "\"worker_saturation\", \"user_complaints\"]\n"
            "RootCause: The external API introduced intermittent latency "
            "(2-8 s). Since the Flask app makes synchronous requests, each "
            "slow call ties up a Gunicorn worker for the full duration, "
            "reducing overall throughput.\n"
            "Remediation: Reduced the client timeout from 10 s to 3 s. "
            "Moved external API calls to a background Celery task so they "
            "do not block web workers. Added a WebSocket/SSE push to notify "
            "the frontend when the data is ready.\n"
            "Verification: p99 latency dropped to 0.8 s; workers no longer "
            "block on external calls.\n"
            "Resolved: True\n"
        ),
    },
    {
        "filename": "kb_api_latency_005.txt",
        "content": (
            "IncidentID: KB-API-005\n"
            "ErrorCode: FAULT_EXTERNAL_API_LATENCY\n"
            "Symptoms: Cascading failure – high latency from upstream API "
            "caused request queue to grow, leading to memory pressure and "
            "OOM-kill of the web container. Kubernetes restarted the pod 3 "
            "times in 10 minutes.\n"
            "Breadcrumbs: [\"external_api_call\", \"high_latency\", "
            "\"request_queue_buildup\", \"oom_kill\", \"pod_restart\"]\n"
            "RootCause: Without a timeout or circuit breaker, slow upstream "
            "responses accumulated in-flight requests. Each held memory for "
            "the response body, request context, and open socket, exceeding "
            "the container's 512 MB memory limit.\n"
            "Remediation: Set a strict 3 s timeout on the external call. "
            "Added a circuit breaker (pybreaker) that trips after 5 failures "
            "and returns a fallback for 30 s before retrying. Increased "
            "container memory limit to 768 MB as a safety margin.\n"
            "Verification: Simulated 100 concurrent slow upstream calls; "
            "memory peaked at 420 MB and no OOM events occurred.\n"
            "Resolved: True\n"
        ),
    },
]


# ---------------------------------------------------------------------------
# Seeder logic
# ---------------------------------------------------------------------------

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
