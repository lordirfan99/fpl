#!/usr/bin/env bash
# Tagged, scoped deployment; never enables FPL execution or restarts bots.
set -euo pipefail
TAG="${1:?usage: install-private-dashboard.sh <tag> [--rollback]}"
MODE="${2:-install}"
[[ "$MODE" = install || "$MODE" = --rollback ]] || exit 1
cd "$(git rev-parse --show-toplevel)"
SHA=$(git rev-parse --verify "refs/tags/$TAG^{commit}")
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || exit 1
[ "$(git rev-parse HEAD)" = "$SHA" ] && [ -z "$(git status --porcelain)" ] || exit 1
DEST=/opt/fpl-autopilot
BACKUP="/var/backups/fpl-dashboard/$SHA"
FILES=(model/dashboard_packet.py jobs/dashboard_account_check.py jobs/pre_deadline_run.py)
UNITS=(fpl-dashboard-account-check.service fpl-dashboard-account-check.timer)
for timer in fpl-auto-runner.timer fpl-dashboard-account-check.timer; do
  if systemctl is-active --quiet "$timer"; then echo "Stop $timer first"; exit 1; fi
done
if pgrep -f 'python.*(pre_deadline_run[.]py|fpl_auto[.]py|dashboard_account_check[.]py)' >/dev/null; then
  echo 'Wait for running planner/account check'; exit 1
fi
restore() {
  for file in "${FILES[@]}"; do
    if sudo test -f "$BACKUP/$file"; then
      sudo cp -p "$BACKUP/$file" "$DEST/$file"
    elif sudo test -f "$BACKUP/$file.absent"; then
      sudo rm -f -- "$DEST/$file"
    fi
  done
  for unit in "${UNITS[@]}"; do
    if sudo test -f "$BACKUP/$unit"; then sudo cp -p "$BACKUP/$unit" "/etc/systemd/system/$unit";
    elif sudo test -f "$BACKUP/$unit.absent"; then sudo rm -f -- "/etc/systemd/system/$unit"; fi
  done
  sudo systemctl daemon-reload
}
if [ "$MODE" = --rollback ]; then
  sudo test -f "$BACKUP/complete" || { echo 'Complete backup unavailable'; exit 1; }
  restore
  echo 'Rolled back; timers remain stopped'
  exit 0
fi
sudo test ! -e "$BACKUP" || { echo 'Backup exists'; exit 1; }
sudo -u fpl "$DEST/.venv/bin/python" -c 'import google.cloud.storage,json; from pathlib import Path; assert json.loads(Path("/opt/fpl-autopilot/config/dashboard.json").read_text())["private_bucket"]'
"$DEST/.venv/bin/python" -c 'import ast,pathlib; [ast.parse(pathlib.Path(p).read_text()) for p in ("engine/model/dashboard_packet.py", "engine/jobs/dashboard_account_check.py", "engine/jobs/pre_deadline_run.py")]'
sudo install -d -m 0700 "$BACKUP/model" "$BACKUP/jobs"
for file in "${FILES[@]}"; do
  if sudo test -f "$DEST/$file"; then sudo cp -p "$DEST/$file" "$BACKUP/$file";
  else sudo touch "$BACKUP/$file.absent"; fi
done
for unit in "${UNITS[@]}"; do
  if sudo test -f "/etc/systemd/system/$unit"; then sudo cp -p "/etc/systemd/system/$unit" "$BACKUP/$unit";
  else sudo touch "$BACKUP/$unit.absent"; fi
done
sudo touch "$BACKUP/complete"
COMPLETE=0
trap 'if [ "$COMPLETE" = 0 ]; then restore; fi' EXIT
for file in "${FILES[@]}"; do
  sudo install -o fpl -g fpl -m 0644 "engine/$file" "$DEST/$file.release-tmp"
  sudo mv -f -- "$DEST/$file.release-tmp" "$DEST/$file"
  cmp "engine/$file" "$DEST/$file"
done
for unit in "${UNITS[@]}"; do
  sudo install -o root -g root -m 0644 "infra/deploy/gcp/systemd/$unit" "/etc/systemd/system/$unit"
done
sudo systemctl daemon-reload
COMPLETE=1
echo "Installed $SHA; backup $BACKUP. Timers remain stopped; verify private access before enabling."
