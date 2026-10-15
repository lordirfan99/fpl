#!/usr/bin/env bash
set -euo pipefail
umask 077
TAG="${1:?tag required}"
SHA="${2:?reviewed commit required}"
[[ "$TAG" =~ ^v[0-9]{4}\.[0-9]{2}\.[0-9]{2}-dashboard-health[a-zA-Z0-9.-]*$ ]]
[[ "$SHA" =~ ^[a-f0-9]{40}$ ]]
BASE=/opt/fpl-autopilot
OUT=$(mktemp -d /tmp/fpl-dashboard-health.XXXXXX)
WAS_ACTIVE=0
timer_before=$(systemctl is-active fpl-auto-runner.timer || true)
[[ "$timer_before" = active || "$timer_before" = inactive ]]
pending_hash() {
  if sudo test -f "$BASE/data/processed/pending_plan.json"; then
    sudo sha256sum "$BASE/data/processed/pending_plan.json" | cut -d ' ' -f1
  else echo absent; fi
}
PENDING_BEFORE=$(pending_hash)
finish() {
  rc=$?
  set +e
  if [ "$WAS_ACTIVE" = 1 ]; then sudo systemctl start fpl-auto-runner.timer || rc=1; fi
  exit "$rc"
}
trap finish EXIT
if [ "$timer_before" = active ]; then WAS_ACTIVE=1; fi
sudo systemctl stop fpl-auto-runner.timer
if pgrep -f 'python.*(pre_deadline_run[.]py|pre_deadline_shadow_v42[.]py|post_gw_review[.]py|fpl_auto[.]py)' >/dev/null; then
  echo 'Planner/reviewer active; retry later.' >&2
  exit 1
fi
git clone --quiet --depth 1 --branch "$TAG" https://github.com/lordirfan99/fpl.git "$OUT/release"
cd "$OUT/release"
test "$(git rev-parse HEAD)" = "$SHA"
bash infra/deploy/install-dashboard-health.sh "$TAG"
# Keep diagnostics installed even if the existing publisher still fails: the
# probe reports the real safe reason instead of rolling back observability.
LOG=$(sudo -u fpl mktemp /tmp/fpl-dashboard-health-probe.XXXXXX)
PROBE_ID="dashboard-health-$SHA-$(date +%s)"
PROBE_RC=0
sudo -u fpl env FPL_AUTOPILOT_HOME="$BASE" FPL_RUN_ID="$PROBE_ID" bash -c \
  '"$1" "$2" --dashboard-only --notifications-disabled > "$3" 2>&1' \
  _ "$BASE/.venv/bin/python" "$BASE/jobs/pre_deadline_run.py" "$LOG" || PROBE_RC=$?
for path in model/pipeline_health.py model/dashboard_packet.py jobs/pre_deadline_run.py jobs/fpl_auto.py; do
  sudo cmp "engine/$path" "$BASE/$path"
done
test "$PENDING_BEFORE" = "$(pending_hash)"
if [ "$WAS_ACTIVE" = 1 ]; then sudo systemctl start fpl-auto-runner.timer; fi
test "$(systemctl is-active fpl-auto-runner.timer || true)" = "$timer_before"
sudo -u fpl "$BASE/.venv/bin/python" - "$PROBE_RC" "$PROBE_ID" "$LOG" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, '/opt/fpl-autopilot/model')
from pipeline_health import diagnostic_from_stderr
path = Path('/opt/fpl-autopilot/data/processed/dashboard_publication_status.json')
raw = json.loads(path.read_text()) if path.exists() else {}
expected_run = hashlib.sha256(sys.argv[2].encode()).hexdigest()
if raw.get('run_id_hash') == expected_run:
    health = {key: raw.get(key) for key in (
        'schema_version', 'updated_at', 'status', 'gameweek', 'source_status',
        'refresh_failure_count', 'code', 'stage', 'error_class', 'run_id_hash')}
else:
    health = {'status': 'not_recorded_for_probe',
              **diagnostic_from_stderr(Path(sys.argv[3]).read_text()[-2000:])}
print(json.dumps({'status': 'installed_and_verified', 'module_files_verified': 4,
                  'probe_exit_code': int(sys.argv[1]), 'publication_health': health,
                  'publisher_verified_healthy': int(sys.argv[1]) == 0 and health['status'] == 'published',
                  'pending_plan_unchanged': True,
                  'fpl_write_path_invoked': False, 'telegram_notification_path_invoked': False}))
PY
echo "Timer restored: $timer_before. Private probe log: $LOG. Release checkout: $OUT/release"
