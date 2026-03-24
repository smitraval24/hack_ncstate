# CLAUDE.md — Project Rules for hack_ncstate

## CONVERSATION START — ROLE SELECTION (MANDATORY)

At the **very beginning** of every new conversation, before doing any work, ask:

> **Are you working as the Engineer or the Designer today?**

- **Engineer** — adapt to senior backend expertise: skip basics, systems-level reasoning, focus on Flask, SQLAlchemy, Lambdas, CloudWatch, pipeline logic.
- **Designer** — adapt to UI/UX focus: dashboard layout, Tailwind CSS, Jinja2 templates, frontend assets. Explain backend concepts simply when needed.

Do NOT proceed with any task until the user has answered. Apply the selected role for the entire conversation.

---

## Project Overview

AI-driven self-healing system that detects production faults via CloudWatch, diagnoses them using RAG (Backboard.io), generates code fixes via Gemini, and auto-deploys patches through GitHub Actions to ECS Fargate — zero human intervention.

**Pipeline:** Production Error (ECS) -> CloudWatch -> FaultRouter Lambda -> Backboard RAG -> Gemini -> GithubTool Lambda (commit) -> GitHub Actions -> ECS redeploy

## Tech Stack

- **Backend:** Flask 3.1, Gunicorn, Celery, SQLAlchemy, Alembic
- **Database:** PostgreSQL 18.1, Redis 8.4
- **Frontend:** Tailwind CSS, esbuild, Jinja2
- **Infrastructure:** Docker, ECS Fargate, AWS Lambda, CloudWatch
- **AI/RAG:** Backboard.io, Google Gemini
- **Testing:** pytest, pytest-cov
- **Linting:** ruff
- **Package Manager:** uv
- **Python:** 3.13+

## Local Development

```bash
# Start all services (web, worker, postgres, redis, mock_api, js, css)
docker compose up --build

# Initialize database
./run flask db reset --with-testdb

# Seed RAG knowledge base (one-time)
curl -X POST http://localhost:8000/incidents/setup-assistant
curl -X POST http://localhost:8000/incidents/seed-kb
```

App runs on port 8000. Mock API on port 5001.

## Common Commands

```bash
./run test              # Run test suite
./run test:coverage     # Tests with coverage
./run lint              # Lint (ruff)
./run format            # Auto-format (ruff)
./run quality           # All quality checks
./run shell             # Bash in web container
./run psql              # Connect to PostgreSQL
./run redis-cli         # Connect to Redis
./run cmd <command>     # Run any command in web container
```

## Running Tests

```bash
./run test                                          # All tests
./run cmd pytest test/hello/page/test_views.py      # Specific file
./run cmd pytest test/path/test_file.py::test_fn -v # Specific test
```

Tests use a separate `_test` database. Fixtures in `test/conftest.py`.

## Commit Conventions

- **Fault fixes:** `[FAULT:<FAULT_CODE>] <description>` (e.g., `[FAULT:FAULT_SQL_INJECTION_TEST] Fix malformed SQL query`)
- **Resets:** `[RESET] Restore all faulty handlers for self-healing loop testing`
- **Other:** Regular descriptive messages

## Key Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/test-fault/run` | POST | Trigger FAULT_SQL_INJECTION_TEST |
| `/test-fault/external-api` | POST | Trigger FAULT_EXTERNAL_API_LATENCY |
| `/test-fault/db-timeout` | POST | Trigger FAULT_DB_TIMEOUT |
| `/incidents/` | GET/POST | Incident CRUD |
| `/incidents/stream` | GET | SSE real-time stream |
| `/incidents/dashboard` | GET | Incident dashboard UI |
| `/developer/incidents` | GET | Developer dashboard |
| `/developer/incidents/reset` | POST | Reset all faults & restore faulty code |
| `/up`, `/health` | GET | Health checks |

---

## ABSOLUTE FILE ROUTING RULES — NON-NEGOTIABLE

When fixing faults or errors in this project, you MUST follow these hardcoded file mappings. There are NO exceptions.

### Error-to-File Routing (HARDCODED)

| Error Type | Fault Code | ONLY modify this file |
|---|---|---|
| SQL errors (injection, syntax, query) | FAULT_SQL_INJECTION_TEST | `hello/page/views_sql.py` |
| API errors (latency, timeout, external) | FAULT_EXTERNAL_API_LATENCY | `hello/page/views_api.py` |
| DB errors (timeout, connection, sleep) | FAULT_DB_TIMEOUT | `hello/page/views_db.py` |

### FORBIDDEN FILES — NEVER TOUCH

- **`hello/page/views.py`** — NEVER read, edit, modify, or even open this file for fault remediation. It is the main dashboard/routing file and is NOT a remediation target.
- **`hello/page/_faulty_views_template.py`** — NEVER touch this file.

### Rules

1. When you see a SQL-related error, go DIRECTLY to `hello/page/views_sql.py`. Do NOT look at `views.py`.
2. When you see an API latency/timeout error, go DIRECTLY to `hello/page/views_api.py`. Do NOT look at `views.py`.
3. When you see a DB timeout error, go DIRECTLY to `hello/page/views_db.py`. Do NOT look at `views.py`.
4. Do NOT use `views.py` as context, reference, or for any purpose during fault remediation.
5. Each fix should be 1-3 lines maximum. Do not refactor, restructure, or add new code.
