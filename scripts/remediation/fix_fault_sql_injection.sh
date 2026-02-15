#!/usr/bin/env bash
set -euo pipefail

echo "[MVP] fix_fault_sql_injection"
echo "1) Ensure SQL fault path is wrapped in try/except"
echo "2) Apply explicit db.session.rollback() on exception"
echo "3) Deploy and verify /test-fault/run behavior"
