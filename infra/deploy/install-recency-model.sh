#!/usr/bin/env bash
# Scoped tagged release; preserves account state, pending approvals and timers.
set -euo pipefail
TAG="${1:?usage: install-recency-model.sh <tag> [--rollback]}"
MODE="${2:-install}"
[[ "$MODE" = install || "$MODE" = --rollback ]] || exit 1
cd "$(git rev-parse --show-toplevel)"
SHA=$(git rev-parse --verify "refs/tags/$TAG^{commit}")
[ "$(git rev-parse HEAD)" = "$SHA" ] && [ -z "$(git status --porcelain)" ] || exit 1
BASE=/opt/fpl-autopilot
BACKUP=/var/backups/fpl-recency-model/$SHA
FILES=(model/recent_form.py model/component_xpts.py model/v4_projection.py
       model/v42_projection.py model/feature_store_v42.py model/dashboard_packet.py
       model/decision_explanation.py jobs/pre_deadline_run.py
       jobs/pre_deadline_shadow_v42.py jobs/post_gw_review.py jobs/backfill_player_history.py)
FILES+=(jobs/evaluate_v42_candidate.py model/evaluation_capture.py
        model/pipeline_health.py jobs/evaluate_frozen_forecasts.py
        jobs/fpl_auto.py jobs/telegram_notify.py jobs/project_paths.py
        model/league_alerts.py)
WAS_ACTIVE=0
CHANGES_STARTED=0
if systemctl is-active --quiet fpl-auto-runner.timer; then WAS_ACTIVE=1; fi
restore_files() {
  for path in "${FILES[@]}"; do
    if sudo test -f "$BACKUP/$path.absent"; then
      sudo rm -f "$BASE/$path"
    else
      sudo install -o fpl -g fpl -m 0644 "$BACKUP/$path" "$BASE/$path"
    fi
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
  echo 'Planner/reviewer active; retry after it finishes.'
  exit 1
fi
if [ "$MODE" = --rollback ]; then
  sudo test -f "$BACKUP/complete"
  restore_files
else
  # Validate every source before touching a runtime file.
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
  sudo -u fpl "$BASE/.venv/bin/python" -c \
    'import sys; sys.path[:0]=["/opt/fpl-autopilot/model","/opt/fpl-autopilot/jobs"]; import recent_form,v4_projection,v42_projection,evaluation_capture,evaluate_frozen_forecasts,fpl_auto; assert recent_form.VERSION == "recency-v1"; assert evaluation_capture.SCHEMA_VERSION == 1'
fi
echo "Recency model $MODE complete; account and pending plan unchanged."
