# Fault Injection Guide

This document describes the three intentional faults in the application and the full demo cycle.
These faults exist to trigger the self-healing pipeline (CloudWatch -> Lambda -> RAG -> GitHub Actions -> Deploy).

---

## Demo Flow

The system supports a repeatable demo cycle:

1. **Push faulty code** — The faulty `views.py` is deployed with all three intentional bugs
2. **Trigger faults** — Use the UI or curl to trigger one or more faults
3. **Dashboard shows errors** — Incidents appear as "detected" with severity/type info
4. **Self-healing triggers** — CloudWatch -> Lambda -> Backboard RAG -> Claude -> GitHub push -> CI/CD deploy
5. **Faults stop triggering** — After deploy, the fixed code no longer produces errors
6. **Reset All** — Clears incidents, SSM cooldowns, and pushes the original faulty `views.py` back to GitHub
7. **CI/CD redeploys faulty code** — The faults are restored, ready for another demo cycle

### Key architecture point

All three fault routes live in `hello/page/views.py` — the same file the self-healing Lambda
tells Claude to read and fix. This means Claude can see the actual bugs and push real code fixes.

---

## Prerequisites

- `ENABLE_FAULT_INJECTION` must be `True` in `config/settings.py` (default: `True`)
- The application must be running with Docker Compose (`docker compose up`)
- PostgreSQL, Redis, and the mock API service must be available
- For "Reset All" to restore faulty code, set one of:
  - `GITHUB_LAMBDA_NAME` (to invoke the GithubTool Lambda), OR
  - `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO` (for direct GitHub API)

---

## Fault 1: FAULT_SQL_INJECTION_TEST

**Route:** `POST /test-fault/run`
**File:** `hello/page/views.py` -> `test_fault_run()`

### What it does
Executes intentionally malformed SQL (`SELECT FROM`) which always fails with a syntax error.

### How to replicate
```bash
curl -X POST http://localhost:8000/test-fault/run
```

### Expected behavior
1. PostgreSQL raises a syntax error on `SELECT FROM`
2. The error is caught, rolled back, and logged to stderr
3. A live incident is created with error_code `FAULT_SQL_INJECTION_TEST`
4. Returns HTTP 500

### Key faulty line
```python
db.session.execute(text("SELECT FROM"))  # invalid SQL on purpose
```

---

## Fault 2: FAULT_EXTERNAL_API_LATENCY

**Route:** `POST /test-fault/external-api`
**File:** `hello/page/views.py` -> `test_fault_external_api()`

### What it does
Calls the configured mock external API (`$MOCK_API_BASE_URL/data`, default
`http://mock_api:5001/data` locally) with a 3-second timeout.
The mock API (`mock_api.py`) is configured with `API_FAULT_MODE=latency,error` which causes:
- **60% chance** of a 2-8 second random delay (causes timeout when delay > 3s)
- **30% chance** of returning HTTP 500 (`{"error": "upstream failure"}`)

Combined, **~70% of requests fail** — either from timeout or upstream HTTP 500.

### How to replicate
```bash
curl -X POST http://localhost:8000/test-fault/external-api
```

Run it multiple times — it's probabilistic, not deterministic.

### Expected behavior
On **timeout**: Returns HTTP 504, creates incident with reason `external_timeout`
On **upstream HTTP 500**: Returns HTTP 504, creates incident with reason `upstream_failure`
On **success** (~30%): Returns HTTP 200 with `{"value": 42}`

### Key faulty line
```python
mock_api_base_url = os.getenv("MOCK_API_BASE_URL", "http://mock_api:5001").rstrip("/")
r = requests.get(f"{mock_api_base_url}/data", timeout=3)  # 3s timeout vs 2-8s mock delay
```

---

## Fault 3: FAULT_DB_TIMEOUT

**Route:** `POST /test-fault/db-timeout`
**File:** `hello/page/views.py` -> `test_fault_db_timeout()`

### What it does
Sets a 2-second statement timeout then runs `SELECT pg_sleep(5)`. The timeout is shorter
than the sleep, so PostgreSQL always cancels the query and raises an error.

### How to replicate
```bash
curl -X POST http://localhost:8000/test-fault/db-timeout
```

### Expected behavior
1. `SET LOCAL statement_timeout = '2s'` sets a 2-second limit
2. `SELECT pg_sleep(5)` starts but is cancelled after 2 seconds
3. The error is caught, rolled back, and logged to stderr
4. A live incident is created with error_code `FAULT_DB_TIMEOUT`
5. Returns HTTP 500

### Key faulty lines
```python
db.session.execute(text("SET LOCAL statement_timeout = '2s';"))
db.session.execute(text("SELECT pg_sleep(5);"))  # always times out (5s > 2s)
```

---

## Reset All

**Endpoint:** `POST /developer/incidents/reset`

### What it does
1. Deletes all live incidents from PostgreSQL
2. Clears AWS SSM fault cooldown parameters (so faults can be processed again immediately)
3. Reads the current `hello/page/views.py` from GitHub
4. Restores any fault handler whose current function body differs from the faulty template
5. Pushes the original faulty function bodies back to GitHub (triggers CI/CD redeploy)

### How it restores faulty code
The original faulty `views.py` content is stored in `hello/page/_faulty_views_template.py`.
On reset, the endpoint pushes this content to GitHub using either:
- The GithubTool Lambda (if `GITHUB_LAMBDA_NAME` is set), or
- The GitHub API directly (if `GITHUB_TOKEN`/`GITHUB_OWNER`/`GITHUB_REPO` are set)

This triggers the CI/CD pipeline which redeploys the faulty code.

---

## How the self-healing pipeline works

1. **Fault triggers** -> error logged to stderr -> shipped to CloudWatch by ECS
2. **CloudWatch subscription filter** -> triggers FaultRouter Lambda
3. **Lambda** -> sends error to Backboard RAG for analysis, then calls Claude API
4. **Claude reads `hello/page/views.py`** -> sees the bug -> generates fix -> pushes to GitHub
5. **GitHub Actions** -> builds, tests, deploys the fix to ECS
6. **Pipeline callback** -> updates incident status to "resolved" on dashboard
7. **After deploy** -> triggering the same fault no longer produces an error (code is fixed)

---

## Troubleshooting

### Faults not triggering?
1. Check `ENABLE_FAULT_INJECTION` is `True` in `config/settings.py`
2. Check `hello/page/views.py` has the faulty code (not a fixed version)
3. Check `git log --oneline -10` for commits like "[FAULT:...]" that may have fixed the faults
4. For DB timeout: ensure `SET LOCAL statement_timeout = '2s'` precedes `pg_sleep(5)`

### Incidents not appearing on dashboard?
1. Check PostgreSQL is running: `docker compose logs postgres`
2. Check the live store: `curl http://localhost:8000/developer/incidents/api/data`
3. Check app logs for "Failed to create incident" errors

### Reset not restoring faulty code?
1. Check GitHub credentials: `GITHUB_TOKEN`/`GITHUB_OWNER`/`GITHUB_REPO` or `GITHUB_LAMBDA_NAME`
2. Check the reset response for `code_reset` field: `curl -X POST http://localhost:8000/developer/incidents/reset`
3. Wait for CI/CD to complete after reset (check GitHub Actions)

### Incidents auto-resolving immediately?
This was a known bug (now fixed). The `_sync_status` function only auto-resolves incidents
that have had some remediation action taken (`auto_fix_pushed` etc.). Newly detected
incidents stay as "detected" until the self-healing loop processes them.
