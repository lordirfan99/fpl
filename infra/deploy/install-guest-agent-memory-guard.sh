#!/usr/bin/env bash
# Apply only the reviewed guest-agent memory guard from a clean release tag.
set -euo pipefail
TAG="${1:?usage: install-guest-agent-memory-guard.sh <tag> [--rollback]}"
MODE="${2:-install}"
[[ "$MODE" = install || "$MODE" = --rollback ]] || exit 1
cd "$(git rev-parse --show-toplevel)"
SHA=$(git rev-parse --verify "refs/tags/$TAG^{commit}")
[ "$(git rev-parse HEAD)" = "$SHA" ] && [ -z "$(git status --porcelain)" ] || exit 1
UNIT=google-guest-agent.service
TARGET=/etc/systemd/system/$UNIT.d/90-fpl-memory-guard.conf
BACKUP=/var/backups/fpl-guest-agent/$SHA
sudo mkdir -p "$BACKUP" "$(dirname "$TARGET")"
if [ "$MODE" = --rollback ]; then
  sudo test -f "$BACKUP/complete" || exit 1
  if sudo test -f "$BACKUP/previous.conf"; then
    sudo cp -p "$BACKUP/previous.conf" "$TARGET"
  else
    sudo rm -f -- "$TARGET"
  fi
else
  if ! sudo test -f "$BACKUP/complete"; then
    if sudo test -f "$TARGET"; then sudo cp -p "$TARGET" "$BACKUP/previous.conf"; fi
    sudo touch "$BACKUP/complete"
  fi
  sudo install -m 0644 infra/deploy/gcp/systemd-overrides/google-guest-agent-resource-limits.conf "$TARGET"
fi
sudo systemctl daemon-reload
sudo systemctl restart "$UNIT"
sudo systemctl is-active --quiet "$UNIT"
sudo systemctl show "$UNIT" -p MemoryCurrent -p MemoryHigh -p MemoryMax -p MemorySwapMax -p OOMScoreAdjust
