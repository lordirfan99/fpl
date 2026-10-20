#!/usr/bin/env bash
# Reporting-only release: no model, credentials, approval or FPL execution changes.
set -euo pipefail
TAG="${1:?usage: install-postgw-report.sh <tag> [--rollback]}"
MODE="${2:-install}"
[[ "$MODE" = install || "$MODE" = --rollback ]] || exit 2
cd "$(git rev-parse --show-toplevel)"
SHA=$(git rev-parse --verify "refs/tags/$TAG^{commit}")
[ "$(git rev-parse HEAD)" = "$SHA" ] && [ -z "$(git status --porcelain)" ] || { echo 'clean tagged checkout required' >&2; exit 1; }
BASE=/opt/fpl-autopilot
BACKUP=/var/backups/fpl-postgw-report/$SHA
FILES=(jobs/post_gw_review.py jobs/post_gw_audit.py)
hash_file() { sudo sha256sum "$1" | cut -d ' ' -f1; }
PLAN_BEFORE=$(hash_file "$BASE/data/processed/pending_plan.json")
SETTINGS_BEFORE=$(hash_file "$BASE/config/settings.json")
WAS_ACTIVE=0
if systemctl is-active --quiet fpl-auto-runner.timer; then WAS_ACTIVE=1; fi
CHANGED=0
restore_files() { for path in "${FILES[@]}"; do sudo install -o fpl -g fpl -m 0644 "$BACKUP/$path" "$BASE/$path"; done; }
finish() {
  rc=$?
  set +e
  if [ "$rc" != 0 ] && [ "$CHANGED" = 1 ]; then restore_files; fi
  if [ "$WAS_ACTIVE" = 1 ]; then sudo systemctl start fpl-auto-runner.timer || rc=1; fi
  exit "$rc"
}
trap finish EXIT
sudo systemctl stop fpl-auto-runner.timer
if pgrep -f 'python.*(post_gw_review[.]py|fpl_auto[.]py)' >/dev/null; then echo 'report/auto-runner active; retry later' >&2; exit 1; fi
if [ "$MODE" = --rollback ]; then
  sudo test -f "$BACKUP/complete"
  restore_files
else
  for path in "${FILES[@]}"; do sudo "$BASE/.venv/bin/python" -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "engine/$path"; done
  sudo mkdir -p "$BACKUP/jobs"
  for path in "${FILES[@]}"; do
    if ! sudo test -f "$BACKUP/$path"; then sudo cp -p "$BASE/$path" "$BACKUP/$path"; fi
  done
  sudo touch "$BACKUP/complete"
  CHANGED=1
  for path in "${FILES[@]}"; do
    sudo install -o fpl -g fpl -m 0644 "engine/$path" "$BASE/$path.release"
    sudo mv -f "$BASE/$path.release" "$BASE/$path"
    sudo cmp "engine/$path" "$BASE/$path"
  done
  sudo -u fpl env HOME=/home/fpl FPL_AUTOPILOT_HOME="$BASE" "$BASE/.venv/bin/python" -c 'import sys; sys.path[:0]=["/opt/fpl-autopilot/jobs"]; import post_gw_review,post_gw_audit; assert post_gw_review.history_rows({"current":[1]}) == [1]; assert post_gw_audit.captain_context({}, {"active_chip":"3xc","picks":[{"element":1,"is_captain":True,"multiplier":3}]})["multiplier"] == 3'
fi
[ "$PLAN_BEFORE" = "$(hash_file "$BASE/data/processed/pending_plan.json")" ]
[ "$SETTINGS_BEFORE" = "$(hash_file "$BASE/config/settings.json")" ]
echo "Post-GW reporting $MODE verified; pending plan/settings unchanged; no FPL write invoked."
