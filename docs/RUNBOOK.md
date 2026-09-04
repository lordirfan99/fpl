# Runbook

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
