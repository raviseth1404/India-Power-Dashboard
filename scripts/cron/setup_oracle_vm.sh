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

echo "==> Installing systemd service + timer"
sudo tee /etc/systemd/system/india-dashboard-update.service >/dev/null <<EOF
[Unit]
Description=India Power Dashboard daily data update
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
EnvironmentFile=$ENV_FILE
WorkingDirectory=$REPO_DIR
ExecStart=$VENV/bin/python $REPO_DIR/scripts/cron/daily_update.py
EOF

sudo tee /etc/systemd/system/india-dashboard-update.timer >/dev/null <<EOF
[Unit]
Description=Run India Power Dashboard update daily (07:00 UTC / 12:30 IST)

[Timer]
OnCalendar=*-*-* 07:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now india-dashboard-update.timer

echo ""
echo "==> Done. Timer enabled:"
systemctl list-timers india-dashboard-update.timer --no-pager || true
echo ""
echo "    Test a run now:   sudo systemctl start india-dashboard-update.service"
echo "    Watch the logs:   journalctl -u india-dashboard-update -f"
echo "    Update later:     git -C $REPO_DIR pull   (picks up parser fixes)"
