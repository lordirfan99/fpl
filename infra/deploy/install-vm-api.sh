#!/usr/bin/env bash
# Install the read-only API behind the VM's existing HTTPS Caddy endpoint.
# Run from a clean tagged checkout after install-live-refresh.sh installed the
# same tag. The staged environment file is secret-bearing and never enters git.
set -euo pipefail

TAG="${1:?usage: install-vm-api.sh <tag> [install|--rollback] [staged-env]}"
MODE="${2:-install}"
STAGED_ENV="${3:-/tmp/fpl-scout-api.env}"
[[ "$MODE" = install || "$MODE" = --rollback ]] || exit 1

cd "$(git rev-parse --show-toplevel)"
SHA=$(git rev-parse --verify "refs/tags/$TAG^{commit}")
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || exit 1
[ "$(git rev-parse HEAD)" = "$SHA" ] && [ -z "$(git status --porcelain)" ] || exit 1

RELEASE="/opt/fpl-live-refresh/releases/$SHA"
CURRENT=/opt/fpl-live-refresh/current
BACKUP="/var/backups/fpl-scout-api/$SHA"
UNIT=/etc/systemd/system/fpl-scout-api.service
ENV_FILE=/etc/fpl-scout-api.env
CADDY_FILE=/etc/caddy/Caddyfile
CADDY_FRAGMENT=/etc/caddy/fpl-scout-api.caddy
IMPORT_PATH=/etc/caddy/fpl-scout-api.caddy

backup_file() {
  local target="$1" name="$2"
  if sudo test -f "$target"; then sudo cp -p "$target" "$BACKUP/$name"
  else sudo touch "$BACKUP/$name.absent"; fi
}

restore_file() {
  local target="$1" name="$2"
  if sudo test -f "$BACKUP/$name"; then sudo cp -p "$BACKUP/$name" "$target"
  elif sudo test -f "$BACKUP/$name.absent"; then sudo rm -f -- "$target"; fi
}

restore() {
  set +e
  sudo systemctl disable --now fpl-scout-api.service >/dev/null 2>&1
  restore_file "$UNIT" fpl-scout-api.service
  restore_file "$ENV_FILE" fpl-scout-api.env
  restore_file "$CADDY_FILE" Caddyfile
  restore_file "$CADDY_FRAGMENT" fpl-scout-api.caddy
  sudo systemctl daemon-reload
  sudo caddy validate --adapter caddyfile --config "$CADDY_FILE" >/dev/null
  sudo systemctl reload caddy
  if sudo test -f "$BACKUP/api-was-active"; then
    sudo systemctl enable --now fpl-scout-api.service
  fi
}

if [ "$MODE" = --rollback ]; then
  sudo test -f "$BACKUP/complete" || { echo 'Complete rollback backup unavailable'; exit 1; }
  restore
  echo "Rolled back VM API using $BACKUP"
  exit 0
fi

[ "$(readlink -f "$CURRENT")" = "$RELEASE" ] || {
  echo 'Install the same tagged live-refresh release first'; exit 1;
}
sudo test -f "$CADDY_FILE"
test -f "$STAGED_ENV"
python3 - "$STAGED_ENV" "$SHA" <<'PY'
import sys
from pathlib import Path

values = {}
for line in Path(sys.argv[1]).read_text().splitlines():
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key] = value
expected = {
    "FPL_MY_TEAM_ID": "2797967",
    "FPL_DEFAULT_LEAGUE_ID": "58005",
    "FPL_SNAPSHOT_BUCKET": "irfan-374115-fpl-snapshots",
    "FPL_PRIVATE_DASHBOARD_BUCKET": "irfan-374115-fpl-private-dashboard",
    "FPL_GIT_SHA": sys.argv[2],
    "FPL_DATA_DIR": "/opt/fpl-scout-api/data",
}
assert all(values.get(key) == value for key, value in expected.items())
assert values.get("FPL_ALLOWED_ORIGINS") == "https://fpl-scout-intelligence.netlify.app"
assert len(values.get("FPL_DASHBOARD_READ_TOKEN", "")) >= 32
assert not any(key.startswith("NEXT_PUBLIC_") for key in values)
PY

sudo test ! -e "$BACKUP" || { echo 'Rollback backup already exists'; exit 1; }
sudo install -d -m 0700 "$BACKUP"
backup_file "$UNIT" fpl-scout-api.service
backup_file "$ENV_FILE" fpl-scout-api.env
backup_file "$CADDY_FILE" Caddyfile
backup_file "$CADDY_FRAGMENT" fpl-scout-api.caddy
if systemctl is-active --quiet fpl-scout-api.service; then sudo touch "$BACKUP/api-was-active"; fi

COMPLETE=0
PATCHED=$(mktemp /tmp/fpl-caddy.XXXXXX)
cleanup() {
  rm -f -- "$PATCHED"
  if [ "$COMPLETE" = 0 ]; then restore; fi
}
trap cleanup EXIT

sudo install -d -o fpl -g fpl -m 0750 /opt/fpl-scout-api/data
sudo install -o root -g root -m 0600 "$STAGED_ENV" "$ENV_FILE"
sudo install -o root -g root -m 0644 \
  infra/deploy/gcp/systemd/fpl-scout-api.service "$UNIT"
sudo install -o root -g root -m 0644 \
  infra/deploy/gcp/caddy/fpl-scout-api.caddy "$CADDY_FRAGMENT"
python3 infra/scripts/patch_caddy_for_vm_api.py "$CADDY_FILE" "$PATCHED" "$IMPORT_PATH"
sudo caddy validate --adapter caddyfile --config "$PATCHED" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now fpl-scout-api.service
curl --fail --silent --show-error --max-time 60 http://127.0.0.1:8790/health >/dev/null
curl --fail --silent --show-error --max-time 60 http://127.0.0.1:8790/ready >/dev/null

sudo install -o root -g root -m 0644 "$PATCHED" "$CADDY_FILE"
sudo caddy validate --adapter caddyfile --config "$CADDY_FILE" >/dev/null
sudo systemctl reload caddy
curl --fail --silent --show-error --max-time 60 \
  https://sportmania.duckdns.org/fpl-scout-api/health >/dev/null

sudo touch "$BACKUP/complete"
COMPLETE=1
echo "Installed VM API $SHA; rollback backup $BACKUP"
