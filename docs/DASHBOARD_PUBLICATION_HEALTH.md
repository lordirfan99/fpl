# Dashboard publication diagnostics and recurring alerts

The reported `Dashboard preview was not published` traceback is a wrapper error.
The old planner printed only the exception class to captured stdout, while the
runner reported the tail of stderr. That discarded the useful publication cause.
Every scheduled retry printed the same traceback to stdout; `deliver_stdout.py`
then forwarded it to Telegram without failure-episode deduplication.

## Behavior after this release

- Required preview publication still fails if canonical sources are unverified,
  private bucket configuration is missing/invalid, account data is incomplete,
  privacy enforcement is absent, storage fails, or local verification binding
  cannot be saved. The existing privacy and source guards are preserved.
- Typed failure codes distinguish these cases and carry the publication phase
  and exception class across the subprocess boundary. Original exception bodies,
  tokens, bucket names, raw account data and refresh-error strings are not echoed.
- `data/processed/dashboard_publication_status.json` records sanitized health,
  source status and the count of refresh failures. A hash binds it to the run;
  an operator probe cannot mistake a stale health record for its own result.
- Runner failure episodes persist in `auto_state.json`. The first failure, a
  changed code/class/phase, a changed GW or preview/decision mode, and a 24-hour
  reminder are eligible for a stdout alert. Identical intermediate retries log
  to stderr, which stays in the VM journal. Retry scheduling is unchanged.
- A successful retry clears its failure episode and reports recovery once.
  League-intelligence refresh failures use the same policy, since that failed
  prerequisite can itself make a preview unpublishable.

The patch does not establish which publication failure occurred in the original
VM incident: that information was discarded by the old runner. Deployment and a
fresh sanitized health/probe result are needed to identify the runtime cause.
Do not report the underlying publisher healthy merely because the alert fix
was installed or a repeated notification was suppressed.

## Diagnose the result

| Code | Investigation |
| --- | --- |
| `source_not_ready` | Check source status, refresh failure count and the runner's league-intelligence failure episode. Restore verified inputs; preserve the source guard. |
| `not_configured` / `configuration_invalid` | Check the VM's private dashboard configuration locally; retain distinct private/public buckets. |
| `account_invalid` / `plan_invalid` | Refresh authenticated account/plan inputs; preserve the prior pending plan. |
| `privacy_guard_failed` | Verify explicit public access prevention; do not weaken it to force publication. |
| `storage_client_failed` | Check installed SDK and application-default credentials. |
| `storage_metadata_failed` / `storage_upload_failed` | Use the safe exception class to distinguish IAM, network, timeout or quota issues. Inspect details locally without publishing credentials. |
| `packet_invalid` | Inspect invalid/non-finite projection or packet fields locally. |
| `local_binding_failed` | Check disk availability and ownership/permissions for processed state. |
| `runner_failed` / `not_recorded_for_probe` | The run failed before a fresh publication result; inspect the private probe log locally. |

## Release and operator deployment

After green CI and merge, create a reviewed `vYYYY.MM.DD-dashboard-health...` tag
using `tag-dashboard-health.yml`, passing the full merged release SHA. The
workflow checks main ancestry and required CI before creating the immutable tag
with GITHUB_TOKEN; it does not deploy the website.

On the authorized Hermes host with the existing Tailscale SSH key, run:

```bash
bash infra/deploy/deploy-dashboard-health-from-operator.sh v2026.10.15-dashboard-health
```

The helper verifies the tag's CI and invokes its scoped remote controller. Only
four files are installed, with backups and rollback: the health helper, private
exporter, pre-deadline job and runner. Model weights, credentials and execution
approvals are unchanged. The controller preserves pending-plan bytes and timer
state and runs only `--dashboard-only --notifications-disabled` as a probe.

The result separately reports installation verification, probe exit code and
`publisher_verified_healthy`. An unsuccessful probe keeps the diagnostics fix
installed and restores the timer, so the next real scheduled run can record its
source-specific cause. This is an installed fix with an unresolved publisher,
not proof of recovery. Original probe stdout/stderr stays in a private VM-local
log. No FPL submission, Telegram send, full auto-runner invocation or website
deployment is performed by the operator controller.

Use `install-dashboard-health.sh <tag> --rollback` from the matching clean tag
to restore the saved pre-release files. Update the runbook's known-good status
only after operator runtime verification; the Codex executor currently lacks
the operator VPN/key and cannot perform this production step itself.
