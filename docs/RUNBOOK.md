# Runbook

## Current recovered runtime — 4 September 2026

The active VM is `instance-20260412-121200` in **us-central1-a**, `e2-micro`,
public IP **34.60.216.122**, private IP `10.128.0.6`. Recovery used PR #63,
tag `v2026.09.04-zone-recovery` (`d3eac83`), and private machine image
`fpl-zone-recovery-20260904`. The original in us-central1-f remains stopped with
its disk intact. Never start both copies: they share bot tokens and state.

At 10:38 UTC Telegram, dashboard bridge, Caddy, SportMania FPL and SportMania
payment bot were active with no failed units. Bot/engine files were preserved
from the original disk; their source commit is not independently established.
API is deployed at `3afe2ed` (`v2026.09.04-vm-live-refresh`) and reports healthy
with FPL writes disabled. The collector is installed from `d3eac83`; record its
verified publication and timer status below after the first successful runs.

The replacement inherits storage read-write scope. Its 10 GB boot disk remained
**pd-balanced** despite the requested standard-disk override; auto-delete was
explicitly turned off and verified separately. Compute is free-tier eligible,
but IPv4, balanced disks and retained recovery images can incur charges. Do not
describe this whole deployment as zero-cost. Retire the original disk/image only
after a deliberate backup-retention decision; do not risk another outage solely
to convert the active disk.

Future VM deployments default to us-central1-a, with `FPL_VM_ZONE` available for
an explicit override. The old migration records below are historical.

## Live refresh migration (4 September 2026)

The owner requires VM scheduling for live league refresh. Follow
[the VM release procedure](../infra/LIVE-REFRESH-VM.md); code preparation does
not mean production has changed. The old Cloud Scheduler endpoint was returning
404. No new Cloud Scheduler or Cloud Run collector should be provisioned.

Observed at 08:55 UTC: API revision `0cfe832` on `fpl-scout-api-00074-6r2`,
Telegram active with zero restarts, VM auto-runner/daily-pull/keepalive successful,
and VM timers active. The legacy freeze and last-known-good table below are
historical migration notes, not a verified description of current production.
The VM's exact deployed commit was not identifiable from its runtime directory.

## Last known-good

| Component | Tag / ref | Notes |
|---|---|---|
| api | scout `93b9c6b2` (PR #12 state) | deployed via scout `deploy-api.yml` on 2026-09-02 |
| engine + bot | autopilot `b6ba31e` (`codex/gcp-deploy`) | reverted onto the VM 2026-09-02 |
| Telegram bot token | rotated 2026-09-02, in VM `config/credentials.env` only | `@Fplnaf_bot`, chat `-1004464574417` |

Update this table on every release.

## Freeze / unfreeze

**Freeze** (stop all automation writing state):
```bash
# schedulers
for j in fpl-capture-journal fpl-monitor fpl-refresh-fixtures fpl-live-league-refresh \
         fpl-decision-refresh fpl-decision-final-window fpl-refresh-gameweek; do
  gcloud scheduler jobs pause "$j" --location=us-central1 --project=irfan-374115
done
# VM write-timers
gcloud compute ssh instance-20260412-121200 --zone us-central1-f --project irfan-374115 \
  --command 'sudo systemctl disable --now fpl-auto-runner.timer fpl-daily-pull.timer fpl-league-finalizer.timer'
```
Keep running during a freeze: `fpl-telegram.service`, `fpl-token-keepalive.timer`,
`fpl-dashboard-bridge*`, the Cloud Run API + Netlify (all read-only / auth-only).

**Unfreeze:** `gcloud scheduler jobs resume …` and `systemctl enable --now …` the
same units. Verify one manual `fpl-auto-runner` run exits 0 and a card arrives
before trusting the schedule.

## Weekly GW workflow (manual mode)

1. Engine builds `pending_plan.json` (or run `engine/jobs/pre_deadline_run.py --force-notify`).
2. Bot sends the approval card to Telegram.
3. Owner reviews, then **acts in the official FPL app** (manual mode — the bot's
   Approve is not wired to auto-submit during the freeze).
4. After the deadline + `data_checked`, `post_gw_review` calibrates.

## Incident response

1. **Freeze first** (above). Do not debug on the box.
2. Capture current VM state to a backup dir + a local copy before changing anything.
3. Diagnose on a branch. Reproduce with a test.
4. Fix via PR + green CI.
5. Deploy from the new tag. Update *Last known-good*.

## Rotate the Telegram token

BotFather → `/token` → `@Fplnaf_bot` → new token. Put it in VM
`config/credentials.env` only (never git), `systemctl restart fpl-telegram.service`,
confirm `getMe` returns `ok:true`.
