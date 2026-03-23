# CLAUDE.md — Project Rules for hack_ncstate

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
