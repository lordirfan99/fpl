#!/usr/bin/env bash
# Run ON the VM from a clean, reviewed release tag. Updates only the two
# planner files; no bot unit, credentials, pending plan or dependency changes.
set -euo pipefail
TAG="${1:?usage: install-recommendation-planner.sh <release-tag> [--rollback]}"
MODE="${2:-install}"
[[ "$MODE" = install || "$MODE" = --rollback ]] || exit 1
cd "$(git rev-parse --show-toplevel)"
SHA=$(git rev-parse --verify "refs/tags/$TAG^{commit}")
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || exit 1
[ "$(git rev-parse HEAD)" = "$SHA" ] || { echo 'Checkout must match tag'; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo 'Checkout must be clean'; exit 1; }
# Stop the timer before calling this script, and let any in-flight job finish.
if systemctl is-active --quiet fpl-auto-runner.timer; then
  echo 'Stop fpl-auto-runner.timer before installing'; exit 1
fi
if pgrep -f 'python.*(pre_deadline_run[.]py|fpl_auto[.]py)' >/dev/null; then
  echo 'A planner process is still running; wait for it to finish'; exit 1
fi
DEST=/opt/fpl-autopilot
BACKUP="/var/backups/fpl-planner/$SHA"
FILES=(model/competitive_v4_client.py jobs/pre_deadline_run.py)
for file in "${FILES[@]}"; do
  [ -f "$DEST/$file" ] || { echo "Missing deployed file: $file"; exit 1; }
done
if [ "$MODE" = --rollback ]; then
  for file in "${FILES[@]}"; do
    sudo test -f "$BACKUP/$file" || { echo "Missing backup: $file"; exit 1; }
  done
  for file in "${FILES[@]}"; do
    sudo install -o fpl -g fpl -m 0644 "$BACKUP/$file" "$DEST/$file.release-tmp"
    sudo mv -f -- "$DEST/$file.release-tmp" "$DEST/$file"
  done
  echo "Rolled planner back using $BACKUP; timer remains stopped"
  exit 0
fi
sudo test ! -e "$BACKUP" || { echo 'Release backup already exists; refusing to overwrite'; exit 1; }
"$DEST/.venv/bin/python" -c 'import ast,pathlib; [ast.parse(pathlib.Path(p).read_text()) for p in ("engine/model/competitive_v4_client.py", "engine/jobs/pre_deadline_run.py")]; print("Syntax OK")'
sudo install -d -m 0700 "$BACKUP/model" "$BACKUP/jobs"
for file in "${FILES[@]}"; do
  sudo cp -p "$DEST/$file" "$BACKUP/$file"
done
COMPLETE=0
restore_on_failure() {
  if [ "$COMPLETE" = 0 ]; then
    for file in "${FILES[@]}"; do
      sudo cp -p "$BACKUP/$file" "$DEST/$file"
    done
    echo "Install failed; restored planner from $BACKUP"
  fi
}
trap restore_on_failure EXIT
# Additive client helper is installed first for compatibility with the old job.
for file in "${FILES[@]}"; do
  sudo install -o fpl -g fpl -m 0644 "engine/$file" "$DEST/$file.release-tmp"
  sudo mv -f -- "$DEST/$file.release-tmp" "$DEST/$file"
  cmp "engine/$file" "$DEST/$file"
done
COMPLETE=1
echo "Installed planner $SHA; backup $BACKUP; timer remains stopped"
echo 'Verify with jobs/pre_deadline_run.py --verify-inputs-only, then restore the timer.'
