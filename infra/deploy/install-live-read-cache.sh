#!/usr/bin/env bash
# Orchestrate an immutable API release and restore its prior symlink on failure.
set -euo pipefail
TAG="${1:?usage: install-live-read-cache.sh <tag> [--rollback]}"
MODE="${2:-install}"
[[ "$MODE" = install || "$MODE" = --rollback ]] || exit 1
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
SHA=$(git rev-parse --verify "refs/tags/$TAG^{commit}")
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || exit 1
[ "$(git rev-parse HEAD)" = "$SHA" ] && [ -z "$(git status --porcelain)" ] || exit 1
for unit in fpl-live-refresh.timer fpl-live-refresh.service fpl-auto-runner.timer fpl-auto-runner.service; do
  if systemctl is-active --quiet "$unit"; then echo "Pause $unit before this release"; exit 1; fi
done
BACKUP=/var/backups/fpl-live-read-cache/$SHA
CURRENT=/opt/fpl-live-refresh/current
ENV_FILE=/etc/fpl-scout-api.env
run_api_installer() {
  sudo env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$ROOT" \
    bash infra/deploy/install-vm-api.sh "$TAG" "$@"
}
restore_previous() {
  local previous
  previous=$(sudo cat "$BACKUP/previous-release")
  [[ "$previous" =~ ^/opt/fpl-live-refresh/releases/[0-9a-f]{40}$ ]] || return 1
  sudo test -d "$previous" && sudo test -f "$BACKUP/previous.env"
  sudo systemctl stop fpl-scout-api.service
  sudo ln -sfn "$previous" "$CURRENT"
  sudo cp -p "$BACKUP/previous.env" "$ENV_FILE"
  sudo systemctl start fpl-scout-api.service
}
if [ "$MODE" = --rollback ]; then
  sudo test -f "$BACKUP/complete"
  run_api_installer --rollback
  restore_previous
  echo 'Prior API restored; resume previously active timers after verification.'
  exit 0
fi
PREVIOUS=$(readlink -f "$CURRENT")
[[ "$PREVIOUS" =~ ^/opt/fpl-live-refresh/releases/[0-9a-f]{40}$ ]] || exit 1
sudo test ! -e "$BACKUP"
sudo install -d -m 0700 "$BACKUP"
sudo cp -p "$ENV_FILE" "$BACKUP/previous.env"
printf '%s\n' "$PREVIOUS" | sudo tee "$BACKUP/previous-release" >/dev/null
sudo python3 - "$ENV_FILE" "$BACKUP/candidate.env" "$SHA" <<'PY'
import os, sys
from pathlib import Path
source, target, sha = sys.argv[1:]
lines = Path(source).read_text().splitlines()
assert sum(line.startswith("FPL_GIT_SHA=") for line in lines) == 1
lines = [f"FPL_GIT_SHA={sha}" if line.startswith("FPL_GIT_SHA=") else line for line in lines]
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write("\n".join(lines) + "\n")
PY
COMPLETE=0
trap 'if [ "$COMPLETE" = 0 ]; then restore_previous; fi' EXIT
bash infra/deploy/install-live-refresh.sh "$TAG"
run_api_installer install "$BACKUP/candidate.env"
sudo touch "$BACKUP/complete"
COMPLETE=1
echo 'API release installed; verify reads, then resume previously active timers.'
