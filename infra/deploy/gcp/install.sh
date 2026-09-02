#!/bin/bash
# FPL Autopilot - GCP VM install script (Debian/Ubuntu). Idempotent.
# FRESH-VM BOOTSTRAP ONLY. For an existing VM use infra/deploy/vm-sync.sh.
#
# This repo is PUBLIC and ships NO secrets. Before the bot can run you must
# place, on the VM only:
#   /opt/fpl-autopilot/config/credentials.env   (TELEGRAM_BOT_TOKEN=..., FPL_LOGIN=..., FPL_PASSWORD=...)
#   /opt/fpl-autopilot/config/settings.json     (team_id, telegram.chat_id, allowed_user_ids)
# then optionally `bash infra/deploy/gcp/secrets-bootstrap.sh` to mirror them to Secret Manager.
#
#   git clone https://github.com/lordirfan99/fpl.git /tmp/fpl && sudo mkdir -p /opt/fpl-autopilot
#   sudo cp -r /tmp/fpl/engine/{model,optimizer,execution,jobs} /tmp/fpl/bot /opt/fpl-autopilot/
#   sudo bash /tmp/fpl/infra/deploy/gcp/install.sh
set -euo pipefail

APP_DIR=/opt/fpl-autopilot
SVC_DIR=/etc/systemd/system
SYSTEMD_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # infra/deploy/gcp
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
runuser -u fpl -- "$PY" -m pip install -r "$SYSTEMD_SRC/../../../engine/requirements.txt"
runuser -u fpl -- "$PY" -m pip install "camoufox[geoip]" playwright

echo "==> [4/7] Browser engine (headless Camoufox/Playwright for FPL OIDC login)"
"$PY" -m playwright install-deps firefox
runuser -u fpl -- "$PY" -m playwright install firefox
runuser -u fpl -- "$PY" -m camoufox fetch
runuser -u fpl -- "$PY" -c "import camoufox, playwright; print('camoufox OK')"

echo "==> [5/7] Secrets — must already be on the VM (this repo ships none)"
for f in config/credentials.env config/settings.json; do
  if [ ! -s "$APP_DIR/$f" ]; then
    echo "FATAL: $APP_DIR/$f is missing. Place it on the VM first (see header)." >&2
    exit 1
  fi
  chmod 600 "$APP_DIR/$f"; chown fpl:fpl "$APP_DIR/$f"
done
[ -f "$APP_DIR/config/fpl_session.json" ] && { chmod 600 "$APP_DIR/config/fpl_session.json"; chown fpl:fpl "$APP_DIR/config/fpl_session.json"; }

echo "==> [6/7] systemd units + timers"
cp "$SYSTEMD_SRC/systemd/"*.service "$SYSTEMD_SRC/systemd/"*.timer "$SVC_DIR/"
install -d -m 0755 /etc/systemd/journald.conf.d /etc/systemd/system/fpl-telegram.service.d
install -o root -g root -m 0644 \
  "$SYSTEMD_SRC/journald/90-fpl-vm-storage.conf" \
  /etc/systemd/journald.conf.d/90-fpl-vm-storage.conf
install -o root -g root -m 0644 \
  "$SYSTEMD_SRC/logrotate/fpl" /etc/logrotate.d/fpl
install -o root -g root -m 0644 \
  "$SYSTEMD_SRC/systemd-overrides/fpl-telegram-resource-limits.conf" \
  /etc/systemd/system/fpl-telegram.service.d/10-resource-limits.conf
install -o root -g root -m 0755 "$SYSTEMD_SRC/fpl-watchdog.sh" "$APP_DIR/fpl-watchdog.sh"
systemctl daemon-reload
systemctl restart systemd-journald

systemctl enable --now fpl-telegram.service
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
systemctl --no-pager status fpl-telegram.service --lines=5 || true
systemctl --no-pager list-timers 'fpl-*' || true
echo
echo "INSTALL DONE. Next steps:"
echo "  1. Watch bot startup:  journalctl -u fpl-telegram.service -f"
echo "  2. Optional: bash infra/deploy/gcp/secrets-bootstrap.sh to mirror secrets to Secret Manager."
echo "  3. See infra/deploy/gcp/README-GCP.md for operations."
