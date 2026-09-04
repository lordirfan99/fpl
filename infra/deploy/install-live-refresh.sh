#!/usr/bin/env bash
# Run ON the VM from a clean checkout of a reviewed release tag.
# Installs code and units only. Does not run the collector or enable its timer.
set -euo pipefail
TAG="${1:?usage: install-live-refresh.sh <release-tag>}"
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
SHA=$(git rev-parse --verify "refs/tags/$TAG^{commit}")
[ "$(git rev-parse HEAD)" = "$SHA" ] || { echo "Checkout must match release tag"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "Checkout must be clean"; exit 1; }
id fpl >/dev/null
if systemctl is-active --quiet fpl-live-refresh.timer || systemctl is-active --quiet fpl-live-refresh.service; then
  echo "Stop the live-refresh timer and service before installing a release"
  exit 1
fi
DEST="/opt/fpl-live-refresh/releases/$SHA"
# A release directory is immutable: use a new tag for changes.
[ ! -e "$DEST" ] || { echo "Release already installed: $DEST"; exit 1; }
sudo install -d "$DEST"
git archive "$SHA" api/app api/requirements.txt infra/scripts/refresh_live_leagues.py |
  sudo tar -xf - -C "$DEST"
sudo python3 -m venv "$DEST/.venv"
sudo "$DEST/.venv/bin/pip" install --no-cache-dir -r "$DEST/api/requirements.txt"
sudo "$DEST/.venv/bin/python" "$DEST/infra/scripts/refresh_live_leagues.py" --help >/dev/null
sudo install -d /opt/fpl-live-refresh
sudo ln -sfn "$DEST" /opt/fpl-live-refresh/current
sudo install -m 0644 infra/deploy/gcp/systemd/fpl-live-refresh.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
echo "Installed $SHA. Timer is not enabled by this script. Follow infra/LIVE-REFRESH-VM.md."
