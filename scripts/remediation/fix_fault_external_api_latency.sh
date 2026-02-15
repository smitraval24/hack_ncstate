#!/usr/bin/env bash
set -euo pipefail

echo "[MVP] fix_fault_external_api_latency"
echo "1) Enable retry-with-backoff for timeout/5xx"
echo "2) Enable degraded fallback response"
echo "3) Deploy and verify /test-fault/external-api SLOs"
