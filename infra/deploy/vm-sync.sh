#!/usr/bin/env bash
# Sync engine + bot CODE from a monorepo checkout onto the autopilot VM.
# Preserves config/, data/, .venv/ on the VM. Backs up what it replaces.
#
#   bash infra/deploy/vm-sync.sh <git-tag-or-sha>
#
# Run from a clean monorepo checkout at the ref you want live.
set -euo pipefail

REF="${1:?usage: vm-sync.sh <git-tag-or-sha>}"
VM=instance-20260412-121200
ZONE="${FPL_VM_ZONE:-us-central1-a}"
PROJECT=irfan-374115
REMOTE=/opt/fpl-autopilot

# The bot systemd template this syncs sets FPL_TELEGRAM_EXECUTION_ENABLED=1 /
# FPL_TELEGRAM_DRY_RUN=0 — the bot CAN submit real FPL writes (gated by the
# hash-confirm tap). Abort unless the operator acknowledges.
if grep -q 'FPL_TELEGRAM_EXECUTION_ENABLED=1' infra/deploy/gcp/systemd/fpl-telegram.service; then
  echo "⚠️  This sync installs an EXECUTION-ENABLED bot unit (writes to FPL on Approve+Confirm)."
  echo "    Kill switch after sync: /etc/fpl-telegram.env with FPL_TELEGRAM_DRY_RUN=1 + restart."
  read -r -p "    Type 'execute' to proceed: " ack
  [ "$ack" = "execute" ] || { echo "aborted."; exit 1; }
fi

test "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo detached)" != "" || true
echo "Local ref: $(git rev-parse --short HEAD)  (asked: $REF)"
git rev-parse --verify "$REF^{commit}" >/dev/null

# Build the payload: monorepo layout -> VM flat layout
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/payload"
mkdir -p "$TMP/payload/systemd"
cp -r engine/model      "$TMP/payload/model"
cp -r engine/optimizer  "$TMP/payload/optimizer"
cp -r engine/execution  "$TMP/payload/execution"
cp -r engine/jobs       "$TMP/payload/jobs"
cp -r bot               "$TMP/payload/bot"
cp infra/deploy/gcp/systemd/fpl-telegram.service "$TMP/payload/systemd/fpl-telegram.service"
[ -d engine/webapp ] && cp -r engine/webapp "$TMP/payload/webapp" || true
# public config only; NEVER ship settings.json / credentials.env / *session*
mkdir -p "$TMP/payload/config"
cp engine/config/bps_rules_*.json "$TMP/payload/config/" 2>/dev/null || true
find "$TMP/payload" -name '__pycache__' -type d -prune -exec rm -rf {} +
( cd "$TMP" && tar -czf payload.tgz -C payload . )

gcloud compute scp --zone "$ZONE" --project "$PROJECT" "$TMP/payload.tgz" "$VM:/tmp/fpl-sync.tgz"

gcloud compute ssh "$VM" --zone "$ZONE" --project "$PROJECT" --command "
set -euo pipefail
TS=\$(date -u +%Y%m%dT%H%M%SZ)
BK=$REMOTE/.monorepo-sync-backup-\$TS
sudo mkdir -p \"\$BK\"
for d in model optimizer execution jobs bot webapp; do
  [ -e $REMOTE/\$d ] && sudo cp -rp $REMOTE/\$d \"\$BK/\$d\" || true
done
echo \"backed up to \$BK\"
sudo tar -xzf /tmp/fpl-sync.tgz -C $REMOTE
sudo chown -R fpl:fpl $REMOTE/model $REMOTE/optimizer $REMOTE/execution $REMOTE/jobs $REMOTE/bot
# Runtime behavior lives in this tagged template; the token remains only in
# /etc/fpl-telegram.env and is never copied from the checkout.
sudo install -m 0644 $REMOTE/systemd/fpl-telegram.service /etc/systemd/system/fpl-telegram.service
sudo rm -rf $REMOTE/systemd
sudo systemctl daemon-reload
# syntax gate before restarting anything
$REMOTE/.venv/bin/python -c \"import ast,glob,sys; [ast.parse(open(f).read(),f) for f in glob.glob('$REMOTE/jobs/*.py')+glob.glob('$REMOTE/bot/*.py')]; print('syntax OK')\"
sudo systemctl restart fpl-telegram.service
sleep 4
systemctl is-active fpl-telegram.service
# dry pipeline run (no card)
sudo -u fpl $REMOTE/.venv/bin/python -u $REMOTE/jobs/pre_deadline_run.py --notifications-disabled >/tmp/pdr.out 2>&1 && echo 'pre_deadline_run RC 0' || { echo 'pre_deadline_run FAILED'; tail -30 /tmp/pdr.out; exit 1; }
"
echo "vm-sync done. Rollback: restore .monorepo-sync-backup-* on the VM and restart units."
