#!/usr/bin/env bash
set -euo pipefail

echo "[MVP] fix_fault_db_timeout"
echo "1) Tune statement/pool timeout and pool size"
echo "2) Reduce long-running fault query blast radius"
echo "3) Deploy and verify /test-fault/db-timeout + /up health"
