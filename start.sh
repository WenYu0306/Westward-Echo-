#!/bin/bash
set -e

echo "=== Westward Echo Pre-Flight Check ==="

# Check Python version
python3 --version

# Check .env exists
if [ ! -f .env ]; then
    echo "ERROR: .env not found. Copy .env.example to .env and add your API keys."
    exit 1
fi

# Source .env and verify critical vars
source .env 2>/dev/null || true
if [ -z "$DEEPSEEK_API_KEY" ]; then
    echo "ERROR: DEEPSEEK_API_KEY not set in .env"
    exit 1
fi

# Create required directories
mkdir -p data output

# Run Python pre-flight
python3 -c "
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
exec python3 -m src.main
