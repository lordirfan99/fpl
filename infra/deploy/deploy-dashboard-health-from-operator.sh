#!/usr/bin/env bash
# Invoke from the already-authorized Hermes host with its existing Tailscale key.
set -euo pipefail
umask 077
TAG="${1:?usage: deploy-dashboard-health-from-operator.sh <reviewed-tag>}"
[[ "$TAG" =~ ^v[0-9]{4}\.[0-9]{2}\.[0-9]{2}-dashboard-health[a-zA-Z0-9.-]*$ ]]
KEY="${FPL_OPERATOR_SSH_KEY:-$HOME/.ssh/pc_irfan_key}"
HOST="${FPL_OPERATOR_SSH_HOST:-irfan@100.72.109.7}"
[ -r "$KEY" ] || { echo 'Existing operator SSH key unavailable; run on the authorized Hermes host.' >&2; exit 1; }
cd "$(git rev-parse --show-toplevel)"
git fetch origin main "refs/tags/$TAG:refs/tags/$TAG"
SHA=$(git rev-parse "refs/tags/$TAG^{commit}")
git merge-base --is-ancestor "$SHA" origin/main
# The tag workflow checks required CI; independently verify it on this host too.
gh api "repos/lordirfan99/fpl/commits/$SHA/check-runs?per_page=100" |
  python3 -c 'import json,sys; sys.path.insert(0,"infra/scripts"); from check_release_ci import require_latest_success; require_latest_success(json.load(sys.stdin)["check_runs"])'

# Send the reviewed tagged installer/controller, not hand-edited model files.
git show "$TAG:infra/deploy/run-dashboard-health-remote.sh" |
  ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=15 -i "$KEY" \
    "$HOST" bash -s -- "$TAG" "$SHA"
