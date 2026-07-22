#!/usr/bin/env bash
# Daily chain for the VM: refresh all source data, then produce tomorrow's
# DAM price forecast. Run by india-dashboard-update.service (12:30 IST).
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO_DIR/.venv/bin/python"

echo "== data update =="
"$PY" "$REPO_DIR/scripts/cron/daily_update.py"

echo "== daily forecast =="
cd "$REPO_DIR/scripts/forecast" && "$PY" daily_forecast.py
