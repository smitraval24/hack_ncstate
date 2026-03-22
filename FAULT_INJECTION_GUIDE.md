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
Calls the mock external API (`http://mock_api:5001/data`) with a 3-second timeout.
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
On **timeout** (~60% of delayed requests exceed 3s):
1. `requests.exceptions.Timeout` is raised
2. Logged as: `FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=external_timeout latency=...`
3. Returns HTTP 504

On **upstream HTTP 500** (~30% chance):
1. `requests.exceptions.HTTPError` is raised after `raise_for_status()`
2. Logged as: `FAULT_EXTERNAL_API_LATENCY route=/test-fault/external-api reason=upstream_failure latency=...`
3. Returns HTTP 504

On **success** (~30% of the time):
1. Returns HTTP 200 with `{"value": 42}`

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
        # INTENTIONAL: 3s timeout against mock API with 60% chance of 2-8s delay
        # and 30% chance of HTTP 500 — fails ~70% of the time
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

    except requests.exceptions.HTTPError:
        latency = time.time() - start
        result = {
            "status": "error",
            "error_code": error_code,
            "detail": "upstream_500",
            "latency": f"{latency:.2f}s",
        }
        msg = (
            f"{error_code} route=/test-fault/external-api "
            f"reason=upstream_failure latency={latency:.2f}"
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

**Key line:** `requests.get("http://mock_api:5001/data", timeout=3)` — 3s timeout against a mock API that randomly delays 2-8s.

### Mock API behavior (`mock_api.py`)
```python
if "latency" in fault and random.random() < 0.6:
    time.sleep(random.uniform(2, 8))       # 60% chance of 2-8s delay

if "error" in fault and random.random() < 0.3:
    return jsonify({"error": "upstream failure"}), 500  # 30% chance of HTTP 500
```

### What breaks it (do NOT do this)
- Increasing the timeout to 10s or higher — most delayed requests will succeed instead of timing out
- Adding retry logic with exponential backoff — masks the failures
- Removing the `raise_for_status()` call — HTTP 500s won't be caught
- Removing the `ENABLE_FAULT_INJECTION` check — the fault runs even when injection is disabled

---

## Fault 3: FAULT_DB_TIMEOUT

**Route:** `POST /test-fault/db-timeout`
**File:** `hello/page/views.py` → `test_fault_db_timeout()`

### What it does
Runs `SELECT pg_sleep(5)` with **no app-level statement timeout**. This causes:
- The database connection to be blocked for 5+ seconds
- If a DB-level or pool-level timeout is configured (lower than 5s), the query is cancelled and an error is raised
- If no timeout is configured, the query completes after 5 seconds — still a problem (5s delay per request)

Either way, this fault produces a **5+ second delay** that degrades the application.

### How to replicate
```bash
curl -X POST http://localhost:8000/test-fault/db-timeout
```

### Expected behavior
**If DB-level timeout < 5s** (most common):
1. `SELECT pg_sleep(5)` starts but is cancelled by PostgreSQL's statement timeout
2. The error is caught and logged as:
   ```
   FAULT_DB_TIMEOUT route=/test-fault/db-timeout reason=db_timeout_or_pool_exhaustion latency=...
   ```
3. A live incident is created with error_code `FAULT_DB_TIMEOUT`
4. Returns HTTP 500

**If no DB-level timeout** (less common):
1. `SELECT pg_sleep(5)` completes after 5 seconds
2. Returns HTTP 200 but with `latency=5.00s` — still indicates a problem
3. Ties up a database connection for 5 seconds, risking pool exhaustion under load

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
        # INTENTIONAL: pg_sleep(5) with no app-level statement_timeout
        # Relies on DB-level or pool-level timeout to trigger the fault
        # Always causes 5+ second delay, often times out
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

**Key line:** `db.session.execute(text("SELECT pg_sleep(5);"))` — blocks for 5 seconds with no app-level timeout protection.

### What breaks it (do NOT do this)
- Adding `SET LOCAL statement_timeout = '1000ms'` before the sleep — this "fixes" the timeout at app level, defeating the fault
- Wrapping in a `_safe_database_operation()` helper with timeout — same problem
- Reducing `pg_sleep` to less than 1 second — the delay becomes negligible
- Adding retry logic — masks the timeout errors
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
2. Check the queries haven't been "fixed" by automated tools — look for `SELECT FROM`, `pg_sleep(5)`, timeout `3` (not higher), and no `SET LOCAL statement_timeout` before `pg_sleep`
3. Check `git log --oneline -10` for commits like "Fix SQL injection" that may have sanitized the faults

### Incidents not appearing on dashboard?
1. Check Redis is running: `docker compose logs redis`
2. Check the live store: `curl http://localhost:8000/developer/incidents/api`
3. Check CloudWatch subscription filter is configured correctly
