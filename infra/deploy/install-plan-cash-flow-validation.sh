#!/usr/bin/env bash
# Scoped release: update only the planner cash-flow validation job.
set -euo pipefail
TAG="${1:?usage: install-plan-cash-flow-validation.sh <tag> [--rollback]}"
MODE="${2:-install}"
[[ "$MODE" = install || "$MODE" = --rollback ]] || exit 1
cd "$(git rev-parse --show-toplevel)"
SHA=$(git rev-parse --verify "refs/tags/$TAG^{commit}")
[ "$(git rev-parse HEAD)" = "$SHA" ] && [ -z "$(git status --porcelain)" ] || exit 1
TARGET=/opt/fpl-autopilot/jobs/pre_deadline_run.py
BACKUP=/var/backups/fpl-plan-cash-flow/$SHA
sudo mkdir -p "$BACKUP"
WAS_ACTIVE=0
if systemctl is-active --quiet fpl-auto-runner.timer; then WAS_ACTIVE=1; fi
restore_timer() { if [ "$WAS_ACTIVE" = 1 ]; then sudo systemctl start fpl-auto-runner.timer; fi; }
trap restore_timer EXIT
sudo systemctl stop fpl-auto-runner.timer
# Never replace a module while the runner is using it.
if pgrep -f 'python.*(pre_deadline_run[.]py|fpl_auto[.]py)' >/dev/null; then echo 'Runner active; retry after it finishes.'; exit 1; fi
if [ "$MODE" = --rollback ]; then
  sudo test -f "$BACKUP/previous.py"
  SOURCE="$BACKUP/previous.py"
else
  SOURCE=engine/jobs/pre_deadline_run.py
  if ! sudo test -f "$BACKUP/previous.py"; then sudo cp -p "$TARGET" "$BACKUP/previous.py"; fi
fi
sudo /opt/fpl-autopilot/.venv/bin/python -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$SOURCE"
sudo install -o fpl -g fpl -m 0644 "$SOURCE" "$TARGET.release"
sudo mv -f "$TARGET.release" "$TARGET"
sudo cmp "$SOURCE" "$TARGET"
echo 'Cash-flow validation installed. Refresh with the dashboard-only job; no pending plan changed.'
