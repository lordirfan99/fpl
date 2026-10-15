#!/usr/bin/env bash
# Scoped publication/runner release; does not install model weights or submit plans.
set -euo pipefail
TAG="${1:?usage: install-dashboard-health.sh <reviewed-tag> [--rollback]}"
MODE="${2:-install}"
[[ "$TAG" =~ ^v[0-9]{4}\.[0-9]{2}\.[0-9]{2}-dashboard-health[a-zA-Z0-9.-]*$ ]]
[[ "$MODE" = install || "$MODE" = --rollback ]]
cd "$(git rev-parse --show-toplevel)"
SHA=$(git rev-parse --verify "refs/tags/$TAG^{commit}")
[ "$(git rev-parse HEAD)" = "$SHA" ] && [ -z "$(git status --porcelain)" ] || exit 1
BASE=/opt/fpl-autopilot
BACKUP=/var/backups/fpl-dashboard-health/$SHA
FILES=(model/pipeline_health.py model/dashboard_packet.py jobs/pre_deadline_run.py jobs/fpl_auto.py)
WAS_ACTIVE=0
CHANGES_STARTED=0
if systemctl is-active --quiet fpl-auto-runner.timer; then WAS_ACTIVE=1; fi
restore_files() {
  # Restore consumers before removing a helper absent before this release.
  for ((index=${#FILES[@]}-1; index>=0; index--)); do
    path="${FILES[$index]}"
    if sudo test -f "$BACKUP/$path.absent"; then sudo rm -f "$BASE/$path";
    else sudo install -o fpl -g fpl -m 0644 "$BACKUP/$path" "$BASE/$path"; fi
  done
}
finish() {
  rc=$?
  set +e
  if [ "$rc" != 0 ] && [ "$CHANGES_STARTED" = 1 ]; then restore_files; fi
  if [ "$WAS_ACTIVE" = 1 ]; then sudo systemctl start fpl-auto-runner.timer || rc=1; fi
  exit "$rc"
}
trap finish EXIT
sudo systemctl stop fpl-auto-runner.timer
if pgrep -f 'python.*(pre_deadline_run[.]py|pre_deadline_shadow_v42[.]py|post_gw_review[.]py|fpl_auto[.]py)' >/dev/null; then
  echo 'Planner/reviewer active; deployment not started. Retry after it finishes.'
  exit 1
fi
if [ "$MODE" = --rollback ]; then
  sudo test -f "$BACKUP/complete"
  restore_files
else
  for path in "${FILES[@]}"; do
    sudo "$BASE/.venv/bin/python" -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "engine/$path"
  done
  sudo mkdir -p "$BACKUP/model" "$BACKUP/jobs"
  for path in "${FILES[@]}"; do
    if ! sudo test -f "$BACKUP/$path" && ! sudo test -f "$BACKUP/$path.absent"; then
      if sudo test -f "$BASE/$path"; then sudo cp -p "$BASE/$path" "$BACKUP/$path";
      else sudo touch "$BACKUP/$path.absent"; fi
    fi
  done
  sudo touch "$BACKUP/complete"
  CHANGES_STARTED=1
  for path in "${FILES[@]}"; do
    sudo install -o fpl -g fpl -m 0644 "engine/$path" "$BASE/$path.release"
    sudo mv -f "$BASE/$path.release" "$BASE/$path"
    sudo cmp "engine/$path" "$BASE/$path"
  done
  sudo -u fpl env FPL_AUTOPILOT_HOME="$BASE" "$BASE/.venv/bin/python" -c \
    'import sys; sys.path[:0]=["/opt/fpl-autopilot/model","/opt/fpl-autopilot/jobs","/opt/fpl-autopilot/execution"]; import pipeline_health,dashboard_packet,pre_deadline_run,fpl_auto'
fi
echo "Dashboard health $MODE complete; pending plan unchanged."
