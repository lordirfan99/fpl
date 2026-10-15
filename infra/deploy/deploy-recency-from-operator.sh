#!/usr/bin/env bash
# Run on the already-authorized Hermes/operator host, not a fresh cloud runner.
set -euo pipefail
umask 077
TAG=v2026.10.15-model-recency
SHA=03268bbb8625447b8543461e0d0756caf41ca823
KEY="${FPL_OPERATOR_SSH_KEY:-$HOME/.ssh/pc_irfan_key}"
HOST="${FPL_OPERATOR_SSH_HOST:-irfan@100.72.109.7}"
if [ ! -r "$KEY" ]; then
  echo "Run on the authorized operator host: existing SSH key unavailable at $KEY" >&2
  exit 1
fi
command -v python3 >/dev/null
cd "$(git rev-parse --show-toplevel)"
git fetch origin "refs/tags/$TAG:refs/tags/$TAG"
test "$(git rev-parse "refs/tags/$TAG^{commit}")" = "$SHA"
OUT=$(mktemp -d /tmp/fpl-recency-operator.XXXXXX)
SSH=(ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=15 -i "$KEY")

capture_state() {
  "${SSH[@]}" "$HOST" 'sudo -u fpl /opt/fpl-autopilot/.venv/bin/python -' <<'PY'
import hashlib
import json
from pathlib import Path
import subprocess

base = Path('/opt/fpl-autopilot')
files = (
    'model/recent_form.py', 'model/component_xpts.py', 'model/v4_projection.py',
    'model/v42_projection.py', 'model/feature_store_v42.py', 'model/dashboard_packet.py',
    'model/decision_explanation.py', 'jobs/pre_deadline_run.py',
    'jobs/pre_deadline_shadow_v42.py', 'jobs/post_gw_review.py',
    'jobs/backfill_player_history.py', 'jobs/evaluate_v42_candidate.py',
)
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

timer = subprocess.run(['systemctl', 'is-active', 'fpl-auto-runner.timer'],
                       capture_output=True, text=True).stdout.strip()
processes = subprocess.run(
    ['pgrep', '-f', r'python.*(pre_deadline_run[.]py|pre_deadline_shadow_v42[.]py|post_gw_review[.]py|fpl_auto[.]py)'],
    capture_output=True, text=True).stdout.split()
pending = base / 'data/processed/pending_plan.json'
preview_path = base / 'data/processed/dashboard_plan.json'
preview = json.loads(preview_path.read_text()) if preview_path.is_file() else {}
rows = preview.get('decision_summary', {}).get('player_form', [])
history_path = base / 'data/processed/player_event_history.jsonl'
history_gws = set()
if history_path.is_file():
    for line in history_path.read_text().splitlines():
        try:
            history_gws.add(int(json.loads(line)['gw']))
        except (ValueError, KeyError, TypeError):
            pass
print(json.dumps({
    'timer': timer, 'planner_pids': processes,
    'pending_plan_sha256': digest(pending),
    'installed_sha256': {name: digest(base / name) for name in files},
    'history_gameweeks': sorted(history_gws),
    'projection_features': preview.get('projection_features'),
    'projection_version': preview.get('projection_version'),
    'form_rows': len(rows),
    'applied_rows': sum(row.get('status') == 'applied' for row in rows),
    'single_haul_dependency_rows': sum(bool(row.get('single_haul_dependency')) for row in rows),
}, sort_keys=True))
PY
}

capture_state > "$OUT/before.json"
python3 - "$OUT/before.json" <<'PY'
import json
from pathlib import Path
import sys
before = json.loads(Path(sys.argv[1]).read_text())
if before['planner_pids']:
    raise SystemExit('Planner/reviewer active; deployment not started. Retry after it finishes.')
if before['timer'] not in ('active', 'inactive'):
    raise SystemExit('Unexpected timer state; deployment not started.')
PY

# This is the exact remote controller from the reviewed immutable model tag.
# It clones that tag, runs its scoped installer, backfills checked events and
# publishes only a dashboard preview. No model files are copied by this wrapper.
git show "$TAG:infra/deploy/release-recency-model-remote.sh" |
  "${SSH[@]}" "$HOST" bash -s -- "$TAG" install | tee "$OUT/install.log"
capture_state > "$OUT/after.json"

python3 - "$TAG" "$OUT" <<'PY'
import hashlib
import json
from pathlib import Path
import subprocess
import sys
tag, out = sys.argv[1], Path(sys.argv[2])
before = json.loads((out / 'before.json').read_text())
after = json.loads((out / 'after.json').read_text())
assert before['pending_plan_sha256'] == after['pending_plan_sha256'], 'Pending plan changed'
assert before['timer'] == after['timer'], 'Original timer state not restored'
assert after['projection_features'] == 'recency-v1', 'Preview version not verified'
assert after['form_rows'] > 0 and after['applied_rows'] > 0, 'Recent-form evidence missing'
for name, actual in after['installed_sha256'].items():
    source = subprocess.check_output(['git', 'show', f'{tag}:engine/{name}'])
    assert hashlib.sha256(source).hexdigest() == actual, f'Installed source mismatch: {name}'
report = {
    'status': 'deployed_and_verified', 'method': 'existing operator Tailscale SSH',
    'tag': tag, 'commit': '03268bbb8625447b8543461e0d0756caf41ca823',
    'module_files_verified': len(after['installed_sha256']),
    'projection_features': after['projection_features'],
    'projection_version': after['projection_version'],
    'history_gameweeks': after['history_gameweeks'],
    'applied_form_rows': after['applied_rows'],
    'single_haul_dependency_rows': after['single_haul_dependency_rows'],
    'pending_plan_sha256_before': before['pending_plan_sha256'],
    'pending_plan_sha256_after': after['pending_plan_sha256'],
    'timer_before': before['timer'], 'timer_after': after['timer'],
    'fpl_write_path_invoked': False, 'telegram_notification_path_invoked': False,
}
(out / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
PY
echo "Verification artifacts: $OUT"
