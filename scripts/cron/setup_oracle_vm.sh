#!/usr/bin/env bash
#
# One-shot setup for the daily updater on an Oracle Cloud (or any Ubuntu/Debian)
# India-region VM. Installs a Python venv + deps and a systemd timer that runs
# scripts/cron/daily_update.py every day at 03:00 UTC (08:30 IST).
#
# Usage (from the cloned repo on the VM):
#   bash scripts/cron/setup_oracle_vm.sh
# First run creates an env file for your key and exits; edit it, then re-run.
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="$REPO_DIR/.venv"
ENV_FILE="$HOME/india-dashboard.env"

echo "==> Installing system packages (python venv, pip, git)"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git

echo "==> Creating virtualenv + installing Python deps"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -r "$REPO_DIR/scripts/cron/requirements.txt"

if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
SUPABASE_URL=https://ltzulzadxqpwvfksmcfa.supabase.co
SUPABASE_SERVICE_ROLE_KEY=PASTE_YOUR_SUPABASE_SECRET_KEY_HERE
CRON_DAYS=4
EOF
  chmod 600 "$ENV_FILE"
  echo ""
  echo "==> Created $ENV_FILE"
  echo "    Edit it and paste your Supabase service_role (sb_secret_...) key,"
  echo "    then re-run this script:  nano $ENV_FILE"
  exit 0
fi

if grep -q "PASTE_YOUR_SUPABASE_SECRET_KEY_HERE" "$ENV_FILE"; then
  echo "!! $ENV_FILE still has the placeholder key — edit it first, then re-run."
  exit 1
fi

echo "==> Installing systemd services + timers (dual daily runs: 08:30 & 12:30 IST)"
# Two full runs per day (data update + forecast), tagged so the forecasts land
# as separate rows — an A/B on fundamentals freshness vs run time:
#   03:00 UTC = 08:30 IST  -> MODEL_TAG=lgbm-0830 (early, provably pre-bid-close)
#   07:00 UTC = 12:30 IST  -> MODEL_TAG=lgbm-1230 (fresh PSP outage/demand data)
for cfg in "0830 03:00:00 lgbm-0830" "1230 07:00:00 lgbm-1230"; do
  set -- $cfg
  NAME="india-dashboard-$1"; UTC_TIME=$2; TAG=$3
  sudo tee /etc/systemd/system/$NAME.service >/dev/null <<EOF
[Unit]
Description=India Power Dashboard daily run ($1 IST)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
EnvironmentFile=$ENV_FILE
Environment=MODEL_TAG=$TAG
WorkingDirectory=$REPO_DIR
ExecStart=/usr/bin/bash $REPO_DIR/scripts/cron/run_daily.sh
EOF
  sudo tee /etc/systemd/system/$NAME.timer >/dev/null <<EOF
[Unit]
Description=India Power Dashboard daily run at $UTC_TIME UTC

[Timer]
OnCalendar=*-*-* $UTC_TIME UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF
done

sudo systemctl daemon-reload
sudo systemctl disable --now india-dashboard-update.timer 2>/dev/null || true
sudo systemctl enable --now india-dashboard-0830.timer india-dashboard-1230.timer

echo ""
echo "==> Done. Timers enabled:"
systemctl list-timers 'india-dashboard-*' --no-pager || true
echo ""
echo "    Test a run now:   sudo systemctl start india-dashboard-1230.service"
echo "    Watch the logs:   journalctl -u india-dashboard-1230 -f"
echo "    Update later:     git -C $REPO_DIR pull   (picks up parser fixes)"
