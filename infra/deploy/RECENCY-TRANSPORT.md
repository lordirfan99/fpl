# Recency model deployment transport

The VM's public SSH firewall is intentionally closed. GitHub-hosted runners
cannot use the operator host's existing Tailscale connection or SSH key.
The model deployment workflow therefore requests an authenticated Google Cloud
IAP tunnel with `gcloud compute ssh --tunnel-through-iap`.

This change does not modify IAM, firewall rules, model source, the immutable
model tag or the tagged remote installer. It does not fall back to public SSH.
Successful Compute metadata access does not establish IAP access: verify the
actual tunnel/SSH result. An IAP authorization or network error must be reported
as a transport blocker; do not claim a VM deployment or widen public SSH access.

The alternate operator-host path is the existing authenticated Tailscale SSH
connection described in the Hermes handover. That path requires the actual
operator host and its existing credentials. A Codex environment with
`vpn_configured: false` and no SSH credential cannot use that host's connection.

Either path must run the reviewed remote installer from
`v2026.10.15-model-recency` and preserve all documented acceptance checks.
