#!/bin/bash
set -e

echo "=== Westward Echo Pre-Flight Check ==="

# Check Python version.
# Hard-pinned to python3.11: bare `python3` is 3.9.6 on the dev Mac, and the
# project's rule is 3.11 (the Dockerfile base image is python:3.11-slim, so the
# container already satisfies this). This script is a local launcher — nothing
# in Dockerfile/docker-compose.yml invokes it.
if ! command -v python3.11 >/dev/null 2>&1; then
    echo "ERROR: python3.11 not found on PATH. Install Python 3.11 (bare python3 is too old here)."
    exit 1
fi
python3.11 --version

# Check .env exists
if [ ! -f .env ]; then
    echo "ERROR: .env not found. Copy .env.example to .env and add your API keys."
    exit 1
fi

# Source .env and verify critical vars.
# Both providers are load-bearing (same critical set as health.py:check_config):
#   LLM_*  = Qwen, serves READ/READBACK/REVIEW_TERMS
#   GLM_*  = GLM,  serves WRITE/FIX
# This used to require DEEPSEEK_API_KEY — a provider the project abandoned in
# 2026-09 (the constant is gone from src/config.py). That made this a zombie
# gate: it validated a credential nothing reads, never validated the two that
# are really called, and it would break the moment anyone did the reasonable
# thing of cleaning the unused key out of .env.
# `.` not `source`: `source` is a bash extension, absent in dash, where it
# would fail and the .env would never load (so every key would read as empty).
# Note `|| true` only saves bash — in dash `.` is a *special* builtin and a
# missing file exits the shell regardless (measured: exit 2). That is fine here
# because the `[ ! -f .env ]` check above already guarantees the file exists;
# the `2>/dev/null || true` only tolerates an unreadable or malformed one.
. ./.env 2>/dev/null || true
# Written out rather than looping with ${!key}: indirect expansion is a bash
# extension, and this script must also survive `sh start.sh`.
missing_keys=""
if [ -z "$LLM_API_KEY" ]; then
    missing_keys="$missing_keys LLM_API_KEY"
fi
if [ -z "$GLM_API_KEY" ]; then
    missing_keys="$missing_keys GLM_API_KEY"
fi
if [ -n "$missing_keys" ]; then
    echo "ERROR: required key(s) not set in .env:$missing_keys"
    exit 1
fi
if [ -n "$DEEPSEEK_API_KEY" ]; then
    echo "NOTE: DEEPSEEK_API_KEY is still set in .env but has been unused since"
    echo "      the DeepSeek removal (2026-09). Safe to delete."
fi

# Create required directories
mkdir -p data output

# Run Python pre-flight
python3.11 -c "
from src.health import HealthChecker
import sys
report = HealthChecker().check_all()
print(f'Status: {report[\"status\"]}')
for name, check in report['checks'].items():
    icon = '✓' if check['status'] == 'ok' else '✗'
    print(f'  {icon} {name}: {check[\"message\"]} ({check[\"latency_ms\"]}ms)')
if report['status'] == 'unhealthy':
    print('FATAL: System is unhealthy. Fix the errors above and retry.')
    sys.exit(1)
elif report['status'] == 'degraded':
    print('WARNING: System is degraded. Some features may be unavailable.')
print()
"

echo "=== Starting Westward Echo ==="
exec python3.11 -m src.main
