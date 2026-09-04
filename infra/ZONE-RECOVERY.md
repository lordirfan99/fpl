# Shared VM zone recovery — 4 September 2026

**Verified outcome:** restored in us-central1-a, e2-micro, same public IP.
The machine-image operation retained a 10 GB pd-balanced disk despite the
requested pd-standard override. Auto-delete was explicitly disabled afterward.
The script now checks/reports the actual disk type and enforces retention.
Compute eligibility must not be confused with free storage/IP/backups.

The owner authorized recovery in another free-tier-eligible GCP zone after
`e2-micro` starts in `us-central1-f` repeatedly failed with capacity exhaustion.
Use `recover-vm-zone.ps1` from a reviewed, tagged release. Default target is
`us-central1-a`; `b` and `c` are explicit alternatives if capacity is unavailable.
An UP zone is not proof that e2-micro capacity is available.

```powershell
./infra/deploy/recover-vm-zone.ps1 -TargetZone us-central1-a
./infra/deploy/recover-vm-zone.ps1 -TargetZone us-central1-a -Apply
```

The source must be stopped. A private machine image preserves the disk and
configuration before the reserved external IP is detached. Only after that
backup is READY is a replacement created. It uses the same instance name,
an `e2-micro`, a `pd-standard` boot disk and reserved IP `34.60.216.122`.
SSH metadata, labels, service account and scopes are inherited. The source VM
and original disk are not deleted, and the replacement disk has auto-delete off.
The new private IP can differ; verify any internal-IP dependencies.

The storage read-write scope from PR #62 was successfully applied to the stopped
source after session access was restored; recheck it on the replacement before
running live collection. No new service account or key is required.

Free-tier eligibility applies to compute hours and standard-disk allowance, not
an unconditional zero bill. Public IPv4, the recovery image and retained original
balanced disk can incur charges. Do not run both copies: duplicate bots can poll
the same token and process the same actions. Keep the old VM stopped until a
separate deliberate rollback or retirement decision.

If creation fails, inspect target state before retrying another zone. The IP is
still reserved and the source disk is intact. Do not restart the original and
replacement together. Rollback requires stopping the replacement first, moving
the address back to the original, and proving the original can start; its old
zone's capacity is not guaranteed. Retain the recovery image until verified.

Verification: SSH to the replacement zone; require Telegram, dashboard bridge,
Caddy and both SportMania services active, inspect failed units and recent logs,
verify the external IP, then install the reviewed live collector using
`LIVE-REFRESH-VM.md`. Record the actual successful zone in the runbook and make
future deploy scripts use that zone. Do not hot-patch application code.
