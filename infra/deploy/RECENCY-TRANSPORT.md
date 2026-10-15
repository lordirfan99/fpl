# Recency model deployment transport

The VM's public SSH firewall is intentionally closed. GitHub-hosted runners
cannot use the operator host's existing Tailscale connection or SSH key.
The existing GitHub workflow still attempts public SSH and is blocked. The
runbook records that temporary IAP access was removed after outage recovery.
Do not force `--tunnel-through-iap` without independently establishing IAP tunnel
authorization and a narrowly scoped ingress path from `35.235.240.0/20` to TCP 22.

The operator wrapper does not modify IAM, firewall rules, model source, the
immutable model tag or the tagged remote installer. Successful Compute metadata
access does not establish IAP access. Do not widen public SSH access.

The alternate operator-host path is the existing authenticated Tailscale SSH
connection described in the Hermes handover. That path requires the actual
operator host and its existing credentials. A Codex environment with
`vpn_configured: false` and no SSH credential cannot use that host's connection.

On that already-authorized host, update the reviewed repository checkout and run:

```bash
bash infra/deploy/deploy-recency-from-operator.sh
```

The wrapper checks the pinned tag commit, captures non-secret VM state, refuses
an active planner/reviewer, and pipes the exact tagged remote controller through
the existing SSH connection. It then reads back all 12 installed module hashes,
preview features/evidence, the pending-plan hash and original timer state.
Local artifacts use private permissions and contain no raw account packet or
credentials. Only a fully passing verification produces a success report.

This wrapper has not been executed on the operator host during the Codex session;
its existence is not runtime deployment evidence. A Codex environment without
the operator credential exits before any network or deployment action.
