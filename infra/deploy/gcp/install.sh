#!/bin/bash
# FPL Autopilot - GCP VM install script (Debian/Ubuntu). Idempotent.
# Run as root on a fresh VM after cloning the repo to /opt/fpl-autopilot.
#
#   git clone https://github.com/lordirfan99/fpl-autopilot.git /opt/fpl-autopilot
#   cd /opt/fpl-autopilot && bash deploy/gcp/install.sh
#
# The repo deliberately SHIPS config/credentials.env + config/fpl_session.json
# (private repo by design decision). Post-deploy you MUST rotate those secrets
# and store the NEW values in GCP Secret Manager, then run:
#   bash deploy/gcp/secrets-bootstrap.sh
set -euo pipefail

APP_DIR=/opt/fpl-autopilot
SVC_DIR=/etc/systemd/system
VENV="$APP_DIR/.venv"
PY="$VENV/bin/python"
LOG_DIR=/var/log/fpl

echo "==> [1/7] System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl ca-certificates gcc libgomp1 xvfb

echo "==> [2/7] Service user + dirs"
if ! id fpl >/dev/null 2>&1; then
  useradd -m -s /bin/bash fpl
fi
mkdir -p "$APP_DIR" "$LOG_DIR" \
  "$APP_DIR/data/raw" "$APP_DIR/data/processed" \
  "$APP_DIR/data/historical" "$APP_DIR/data/research" \
  "$APP_DIR/reports"
chown -R fpl:fpl "$APP_DIR"
chown -R fpl:fpl "$LOG_DIR"

echo "==> [3/7] Virtualenv + requirements"
if [ ! -x "$PY" ]; then
  python3 -m venv "$VENV"
fi
chown -R fpl:fpl "$VENV"
runuser -u fpl -- "$PY" -m pip install --upgrade pip
runuser -u fpl -- "$PY" -m pip install -r "$APP_DIR/requirements.txt"
runuser -u fpl -- "$PY" -m pip install "camoufox[geoip]" playwright

echo "==> [4/7] Browser engine (headless Camoufox/Playwright for FPL OIDC login)"
"$PY" -m playwright install-deps firefox
runuser -u fpl -- "$PY" -m playwright install firefox
runuser -u fpl -- "$PY" -m camoufox fetch
runuser -u fpl -- "$PY" -c "import camoufox, playwright; print('camoufox OK')"

echo "==> [5/7] Secrets (repo-shipped per user decision)"
chmod 600 "$APP_DIR/config/credentials.env" "$APP_DIR/config/fpl_session.json" 2>/dev/null || true
chown fpl:fpl "$APP_DIR/config/credentials.env" "$APP_DIR/config/fpl_session.json" 2>/dev/null || true

echo "==> [6/7] systemd units + timers"
cp "$APP_DIR/deploy/gcp/systemd/"*.service "$APP_DIR/deploy/gcp/systemd/"*.timer "$SVC_DIR/"
install -d -m 0755 /etc/systemd/journald.conf.d /etc/systemd/system/fpl-bot.service.d
install -o root -g root -m 0644 \
  "$APP_DIR/deploy/gcp/journald/90-fpl-vm-storage.conf" \
  /etc/systemd/journald.conf.d/90-fpl-vm-storage.conf
install -o root -g root -m 0644 \
  "$APP_DIR/deploy/gcp/logrotate/fpl" /etc/logrotate.d/fpl
install -o root -g root -m 0644 \
  "$APP_DIR/deploy/gcp/systemd-overrides/fpl-bot-resource-limits.conf" \
  /etc/systemd/system/fpl-bot.service.d/10-resource-limits.conf
chmod +x "$APP_DIR/deploy/gcp/fpl-watchdog.sh"
systemctl daemon-reload
systemctl restart systemd-journald

systemctl enable --now fpl-bot.service
for t in fpl-daily-pull.timer fpl-auto-runner.timer fpl-token-keepalive.timer \
         fpl-auth-canary.timer fpl-squad-watch.timer \
         fpl-approval-reminder.timer fpl-bot-watchdog.timer \
         fpl-league-finalizer.timer; do
  systemctl enable --now "$t" || true
done
# Betting odds are retained only as offline research artifacts. Ensure an old
# installation cannot silently reactivate the historical fetch job.
systemctl disable --now fpl-odds-fetch.timer 2>/dev/null || true

echo "==> [7/7] Verification"
systemctl --no-pager status fpl-bot.service --lines=5 || true
systemctl --no-pager list-timers 'fpl-*' || true
echo
echo "INSTALL DONE. Next steps:"
echo "  1. Watch bot startup:  journalctl -u fpl-bot.service -f"
echo "  2. After confirmations, run deploy/gcp/secrets-bootstrap.sh to pull rotated secrets."
echo "  3. See deploy/gcp/README-GCP.md for operations."
