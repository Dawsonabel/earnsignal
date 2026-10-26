#!/bin/bash
# Daily job: incremental collection, track-record update, scoring (and a Saturday backtest).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
set -a; [ -f .env ] && . ./.env; set +a
{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) daily run ==="
  .venv/bin/python -m earnsignal.pipeline daily
} >> "logs/daily_$(date +%Y%m).log" 2>&1
