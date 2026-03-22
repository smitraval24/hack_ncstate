# Fault Injection Guide

This document describes the three intentional faults in the application and how to replicate them.
These faults exist to trigger the self-healing pipeline (CloudWatch → Lambda → RAG → GitHub Actions → Deploy).

**WARNING:** These faults are INTENTIONAL. Do NOT "fix" them by replacing faulty queries with valid ones,
increasing timeouts, or adding retry logic. That defeats the purpose of fault injection testing.

---

## Prerequisites

- `ENABLE_FAULT_INJECTION` must be `True` in `config/settings.py` (default: `True`)
- The application must be running with Docker Compose (`docker compose up`)
- PostgreSQL, Redis, and the mock API service must be available

---

## Fault 1: FAULT_SQL_INJECTION_TEST

**Route:** `POST /test-fault/run`
**File:** `hello/page/views.py` → `test_fault_run()`

### What it does
Executes intentionally malformed SQL (`SELECT FROM`) which always fails with a syntax error.

### How to replicate
```bash
curl -X POST http://localhost:8000/test-fault/run
```

### Expected behavior
1. PostgreSQL raises a syntax error on `SELECT FROM`
2. The error is caught, rolled back, and logged as:
   ```
   FAULT_SQL_INJECTION_TEST route=/test-fault/run reason=invalid_sql_executed error=...
   ```
3. A live incident is created with error_code `FAULT_SQL_INJECTION_TEST`
4. Returns HTTP 500

### Original correct fault code (commit `41573b1`)

```python
@page.post("/test-fault/run")
def test_fault_run():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_SQL_INJECTION_TEST"
    result = {"status": "ok", "error_code": None}

    try:
        # INTENTIONAL: malformed SQL — must always fail
        db.session.execute(text("SELECT FROM"))
    except Exception as e:
        result = {"status": "error", "error_code": error_code}

        msg = (
            f"{error_code} route=/test-fault/run "
            f"reason=invalid_sql_executed"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

    return render_template(
        "page/test_fault.html",
        flask_ver=version("flask"),
        python_ver=PYTHON_VER,
        debug=DEBUG,
        enable_fault_injection=True,
        result=result,
    ), (500 if result["status"] == "error" else 200)
```

**Key line:** `db.session.execute(text("SELECT FROM"))` — this is invalid SQL on purpose.

### What breaks it (do NOT do this)
- Replacing `SELECT FROM` with a valid query like `SELECT 1` — the fault will never trigger
- Changing the default result from `error` to `ok` — the response won't reflect the fault
- Removing the `db.session.rollback()` — leaves the session in a broken state

---

## Fault 2: FAULT_EXTERNAL_API_LATENCY

**Route:** `POST /test-fault/external-api`
**File:** `hello/page/views.py` → `test_fault_external_api()`

### What it does
Calls the mock external API (`http://mock_api:5001/data`) with an extremely low timeout,
which guarantees a `requests.exceptions.Timeout`.

### How to replicate
```bash
curl -X POST http://localhost:8000/test-fault/external-api
```

### Expected behavior
1. The request to the mock API times out
2. The error is caught and logged as:
   ```
   FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=external_timeout latency=...
   ```
3. A live incident is created with error_code `FAULT_EXTERNAL_API_LATENCY`
4. Returns HTTP 504

### Original correct fault code (commit `41573b1`)

```python
@page.post("/test-fault/external-api")
def test_fault_external_api():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_EXTERNAL_API_LATENCY"
    result = {"status": "ok", "error_code": None}

    start = time.time()

    try:
        # INTENTIONAL: low timeout (3s) against a slow mock API — must timeout
        r = requests.get("http://mock_api:5001/data", timeout=3)
        latency = time.time() - start
        current_app.logger.info(f"external_call_latency={latency:.2f}")
        r.raise_for_status()
        result = {
            "status": "ok",
            "error_code": None,
            "data": r.json(),
            "latency": f"{latency:.2f}s",
        }

    except requests.exceptions.Timeout:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "timeout",
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=external_timeout latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

    except requests.exceptions.ConnectionError:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "connection_refused",
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=connection_error latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(msg)

    return render_template(...), (504 if result["status"] == "error" else 200)
```

**Key line:** `requests.get("http://mock_api:5001/data", timeout=3)` — low timeout against slow mock API.

### Configuration
- `EXTERNAL_API_BASE_TIMEOUT` env var controls the timeout (default: `0.01` seconds in current code)
- `MOCK_API_BASE_URL` env var controls the target URL (default: `http://mock_api:5001`)

### What breaks it (do NOT do this)
- Increasing the default timeout above 0.01s (e.g., to 10s or 30s)
- Adding a minimum timeout floor above 0.01s
- Adding retry logic with exponential backoff that masks the timeout
- Removing the `ENABLE_FAULT_INJECTION` check — the fault runs even when injection is disabled

---

## Fault 3: FAULT_DB_TIMEOUT

**Route:** `POST /test-fault/db-timeout`
**File:** `hello/page/views.py` → `test_fault_db_timeout()`

### What it does
Sets a PostgreSQL statement timeout of 1 second, then runs `SELECT pg_sleep(5)` which takes 5 seconds.
The query is cancelled by PostgreSQL after 1 second, raising a timeout error.

### How to replicate
```bash
curl -X POST http://localhost:8000/test-fault/db-timeout
```

### Expected behavior
1. `SET LOCAL statement_timeout = '1000ms'` limits the query to 1 second
2. `SELECT pg_sleep(5)` attempts to sleep for 5 seconds
3. PostgreSQL cancels the statement after 1 second
4. The error is caught and logged as:
   ```
   FAULT_DB_TIMEOUT route=/test-fault/db-timeout reason=db_timeout_or_pool_exhaustion latency=...
   ```
5. A live incident is created with error_code `FAULT_DB_TIMEOUT`
6. Returns HTTP 500

### Original correct fault code (commit `41573b1`)

```python
@page.post("/test-fault/db-timeout")
def test_fault_db_timeout():
    if not ENABLE_FAULT_INJECTION:
        return "", 404

    error_code = "FAULT_DB_TIMEOUT"
    result = {"status": "ok", "error_code": None}

    start = time.time()

    try:
        # INTENTIONAL: pg_sleep(5) with no statement_timeout set at app level
        # relies on a low DB-level or pool-level timeout to trigger the fault.
        # Current code adds: SET LOCAL statement_timeout = '1000ms' before this.
        db.session.execute(text("SELECT pg_sleep(5);"))
        latency = time.time() - start
        result = {
            "status": "ok",
            "error_code": None,
            "latency": f"{latency:.2f}s",
        }
    except Exception as e:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": str(e)[:200],
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/db-timeout "
            f"reason=db_timeout_or_pool_exhaustion latency={latency:.2f}"
        )
        print(msg, file=sys.stderr)
        current_app.logger.error(f"db_error={e!s}")

    return render_template(...), (500 if result["status"] == "error" else 200)
```

**Key line:** `db.session.execute(text("SELECT pg_sleep(5);"))` — sleeps 5s, timeout kills it at 1s.

> **Note:** The original code relied on a DB-level or pool-level timeout. The current code explicitly
> sets `SET LOCAL statement_timeout = '1000ms'` before the sleep to guarantee the timeout triggers
> regardless of DB config. Both approaches are valid — the important thing is sleep > timeout.

### What breaks it (do NOT do this)
- Increasing `statement_timeout` to 30s or higher — the sleep completes and no timeout occurs
- Decreasing `pg_sleep` to 2s or less — same problem if timeout is raised too
- Removing the `ENABLE_FAULT_INJECTION` check — the fault runs even when injection is disabled

---

## How the self-healing pipeline works

1. **Fault triggers** → error logged to CloudWatch
2. **CloudWatch subscription filter** → triggers FaultRouter Lambda
3. **Lambda** → sends error to Backboard RAG for analysis, then calls Claude API for a fix
4. **Claude generates fix** → Lambda pushes to GitHub and triggers Actions pipeline
5. **GitHub Actions** → deploys the fix, calls back to `/developer/pipeline/callback`
6. **Dashboard** → shows incident lifecycle (detected → in_progress → resolved)

---

## Troubleshooting

### Faults not triggering?
1. Check `ENABLE_FAULT_INJECTION` is `True` in `config/settings.py`
2. Check the queries haven't been "fixed" by automated tools — look for `SELECT FROM`, `pg_sleep(5)`, timeout `0.01`
3. Check `git log --oneline -10` for commits like "Fix SQL injection" that may have sanitized the faults

### Incidents not appearing on dashboard?
1. Check Redis is running: `docker compose logs redis`
2. Check the live store: `curl http://localhost:8000/developer/incidents/api`
3. Check CloudWatch subscription filter is configured correctly
