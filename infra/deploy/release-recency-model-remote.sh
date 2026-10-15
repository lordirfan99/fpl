#!/usr/bin/env bash
set -euo pipefail
TAG="${1:?release tag required}"
MODE="${2:?install or rollback required}"
[[ "$TAG" =~ ^v[0-9]{4}\.[0-9]{2}\.[0-9]{2}-model-recency[a-zA-Z0-9.-]*$ ]]
[[ "$MODE" = install || "$MODE" = rollback ]]
RELEASE_DIR=$(mktemp -d /tmp/fpl-recency.XXXXXX)
WAS_ACTIVE=0
INSTALLED=0
finish() {
  rc=$?
  set +e
  if [ "$rc" != 0 ] && [ "$INSTALLED" = 1 ]; then
    bash "$RELEASE_DIR/infra/deploy/install-recency-model.sh" "$TAG" --rollback
  fi
  if [ "$WAS_ACTIVE" = 1 ]; then sudo systemctl start fpl-auto-runner.timer; fi
  rm -rf "$RELEASE_DIR"
  exit "$rc"
}
trap finish EXIT
git clone --quiet --depth 1 --branch "$TAG" https://github.com/lordirfan99/fpl.git "$RELEASE_DIR"
cd "$RELEASE_DIR"
BASE=/opt/fpl-autopilot
pending_hash() {
  if sudo test -f "$BASE/data/processed/pending_plan.json"; then
    sudo sha256sum "$BASE/data/processed/pending_plan.json" | cut -d ' ' -f1
  else echo absent; fi
}
PENDING_BEFORE=$(pending_hash)
if systemctl is-active --quiet fpl-auto-runner.timer; then WAS_ACTIVE=1; fi
sudo systemctl stop fpl-auto-runner.timer
if [ "$MODE" = rollback ]; then
  bash infra/deploy/install-recency-model.sh "$TAG" --rollback
else
  bash infra/deploy/install-recency-model.sh "$TAG"
  INSTALLED=1
  sudo -u fpl "$BASE/.venv/bin/python" "$BASE/jobs/backfill_player_history.py" \
    --output "$BASE/data/processed/player_event_history.jsonl"
  # No notifications or executable plan replacement. Keep private team details
  # in a VM-local log, outside the public Actions output.
  PREVIEW_LOG=$(sudo -u fpl mktemp /tmp/fpl-recency-preview.XXXXXX)
  if ! sudo -u fpl env FPL_AUTOPILOT_HOME="$BASE" bash -c \
      '"$1" "$2" --dashboard-only --notifications-disabled > "$3" 2>&1' \
      _ "$BASE/.venv/bin/python" "$BASE/jobs/pre_deadline_run.py" "$PREVIEW_LOG"; then
    echo "Private dashboard preview failed; VM-local log: $PREVIEW_LOG"
    exit 1
  fi
  sudo rm -f "$PREVIEW_LOG"
  sudo -u fpl "$BASE/.venv/bin/python" - <<'PY'
import json
from pathlib import Path
base = Path('/opt/fpl-autopilot/data/processed')
plan = json.loads((base / 'dashboard_plan.json').read_text())
assert plan['projection_features'] == 'recency-v1'
rows = plan['decision_summary']['player_form']
assert rows and any(row['status'] == 'applied' for row in rows)
print(json.dumps({'projection_version': plan['projection_version'],
                  'projection_features': plan['projection_features'],
                  'private_preview_verified': True,
                  'players_with_recent_history': sum(row['status'] == 'applied' for row in rows)}))
PY
fi
test "$PENDING_BEFORE" = "$(pending_hash)"
echo 'Pending plan fingerprint unchanged; no account transfers submitted.'
