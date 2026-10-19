#!/usr/bin/env bash
# Install the reviewed learned-v5 modules only; never changes settings or plan state.
set -euo pipefail
TAG="${1:?usage: install-learned-v5-model.sh <tag> [--rollback]}"
MODE="${2:-install}"
[[ "$MODE" = install || "$MODE" = --rollback ]] || { echo 'mode must be install or --rollback' >&2; exit 2; }
cd "$(git rev-parse --show-toplevel)"
SHA=$(git rev-parse --verify "refs/tags/$TAG^{commit}")
[ "$(git rev-parse HEAD)" = "$SHA" ] && [ -z "$(git status --porcelain)" ] || { echo 'clean tagged checkout required' >&2; exit 1; }
BASE=/opt/fpl-autopilot
BACKUP=/var/backups/fpl-learned-v5/$SHA
FILES=(model/learned_v5_projection.py jobs/pre_deadline_run.py)
WAS_ACTIVE=0
CHANGES_STARTED=0
PENDING_BEFORE=absent
SETTINGS_BEFORE=absent
hash_file() { sudo test -f "$1" && sudo sha256sum "$1" | cut -d ' ' -f1 || echo absent; }
PENDING_BEFORE=$(hash_file "$BASE/data/processed/pending_plan.json")
SETTINGS_BEFORE=$(hash_file "$BASE/config/settings.json")
if systemctl is-active --quiet fpl-auto-runner.timer; then WAS_ACTIVE=1; fi
restore_files() {
  for path in "${FILES[@]}"; do
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
if pgrep -f 'python.*(pre_deadline_run[.]py|fpl_auto[.]py)' >/dev/null; then echo 'planner active; retry later' >&2; exit 1; fi
if [ "$MODE" = --rollback ]; then
  sudo test -f "$BACKUP/complete"
  restore_files
  echo "learned-v5 code rolled back; Python dependencies intentionally retained (dependency rollback is a separate maintenance action)."
else
  # Fail before a source write if the 1 GiB VM cannot safely install wheels.
  free_mb=$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
  free_disk_mb=$(df -Pm "$BASE" | awk 'NR==2 {print $4}')
  [ "$free_mb" -ge 256 ] || { echo "insufficient MemAvailable: ${free_mb}MiB (need >=256MiB)" >&2; exit 1; }
  [ "$free_disk_mb" -ge 300 ] || { echo "insufficient disk: ${free_disk_mb}MiB (need >=300MiB)" >&2; exit 1; }
  sudo "$BASE/.venv/bin/python" -c 'import numpy,scipy; print(numpy.__version__, scipy.__version__)'
  for path in "${FILES[@]}"; do sudo "$BASE/.venv/bin/python" -c 'import ast,sys; ast.parse(open(sys.argv[1],encoding="utf-8").read())' "engine/$path"; done
  sudo mkdir -p "$BACKUP/model" "$BACKUP/jobs"
  for path in "${FILES[@]}"; do
    if ! sudo test -f "$BACKUP/$path" && ! sudo test -f "$BACKUP/$path.absent"; then
      if sudo test -f "$BASE/$path"; then sudo cp -p "$BASE/$path" "$BACKUP/$path"; else sudo touch "$BACKUP/$path.absent"; fi
    fi
  done
  sudo touch "$BACKUP/complete"
  CHANGES_STARTED=1
  for path in "${FILES[@]}"; do
    sudo install -o fpl -g fpl -m 0644 "engine/$path" "$BASE/$path.release"
    sudo mv -f "$BASE/$path.release" "$BASE/$path"
    sudo cmp "engine/$path" "$BASE/$path"
  done
  # Only the three pinned wheels; never resolve or upgrade NumPy/SciPy.
  sudo -u fpl env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    "$BASE/.venv/bin/pip" install --no-cache-dir --only-binary=:all: --no-deps -r engine/requirements-learned-v5.txt
  sudo -u fpl env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    "$BASE/.venv/bin/python" -c 'import sys; sys.path[:0]=["/opt/fpl-autopilot/model","/opt/fpl-autopilot/jobs"]; import learned_v5_projection; assert learned_v5_projection.VERSION == "learned-v5.0-two-stage-classifier-ridge"'
fi
[ "$PENDING_BEFORE" = "$(hash_file "$BASE/data/processed/pending_plan.json")" ] || { echo 'pending plan changed' >&2; exit 1; }
[ "$SETTINGS_BEFORE" = "$(hash_file "$BASE/config/settings.json")" ] || { echo 'settings changed' >&2; exit 1; }
echo "learned-v5 $MODE complete; pending plan and settings unchanged; no FPL write path invoked."
