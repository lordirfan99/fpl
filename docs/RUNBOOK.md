# Runbook

## Recommendation freshness — 4 September 2026

**Problem.** `/v1/recommendations/current` (and `/v1/decision/current`) read only
the newest *finalized* league snapshot. During GW3 prep that file was
`2026-09-01T14:00:07Z` — 69.5 h old, `stale=true` — but the endpoint still
returned 5 transfer suggestions with no honest freshness signal, while
`/v1/leagues/{id}/live/status` was serving a complete VM snapshot ~0.3 h old.

**Change (branch `fix/recommendation-freshness`).**
- New `api/app/recommendation_inputs.py::resolve_recommendation_inputs` picks the
  freshest *safe* league context: fresh VM live snapshot first, finalized
  fallback, honest `safe_hold` / `needs_refresh` when neither is usable. The
  player catalogue / prices / fixtures always come from the independent
  `repository.bootstrap` / `repository.fixtures` reference caches.
- The VM collector (`refresh_live_leagues.py`, `SCHEMA_VERSION` 1→2) now also
  publishes `gw_bank`, the official `overall_rank` and real `transfers_made` per
  manager — all lifted from the `entry_history` block of the picks call it
  already makes, so **no extra FPL request** and no new write path.
- Every response carries `freshness{source, snapshot_at, data_age_hours, stale,
  status, reason, bank_known, rank_provenance, missing_fields}` and a
  `packet_status` (`advisory` / `provisional` / `safe_hold` / `needs_refresh`).
- Dashboard Assistant shows an explicit **Fresh / Provisional / Stale · safe
  hold** state and suppresses the transfer card on a hold.
- `monitor_production.py` fails loudly on a silently-stale recommendation;
  `safe_hold` is an accepted honest degradation.
- No model/scoring change. `fetch_competitive_v4(require_executable_plan=True)`
  and the Telegram `/approve` gate remain the only execution authority; a
  `provisional` / `safe_hold` packet never satisfies the executable path.

**Local verification.** `pytest` 339 passed / 6 skipped (incl. new
`api/tests/test_recommendation_freshness.py` — fresh / stale-finalized /
incomplete-live / mixed-source / future+malformed timestamp / no-write /
Telegram-only cases); `ruff check .` clean; `web` typecheck + build clean.

**Release / production verification.** _Filled in after deploy from the tag:_

| Item | Before | After |
|---|---|---|
| merge commit / tag | — | _pending_ |
| API revision | `3afe2ed` | _pending_ |
| `/v1/recommendations/current` `meta.source` | `snapshot` (finalized) | _pending_ |
| `snapshot_at` / `data_age_hours` | `2026-09-01T14:00:07Z` / 69.5 h | _pending_ |
| `freshness.status` / `packet_status` | (none) / `advisory` | _pending_ |
| `/v1/leagues/{58005,131997}/live/status` | ready | _pending_ |
| VM `systemctl --failed` | — | _pending_ |
| `entry/2797967` transfers made by this system | 0 | _pending (must stay 0)_ |

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
with FPL writes disabled. The collector is installed from `c71e597`
(`v2026.09.04-recovered-runtime`, PR #64). Its preceding manual validation run
from `d3eac83` completed at 10:50:42 UTC with exit 0: league 58005 had 1,218
managers and league 131997 had 2,624, with both API status endpoints ready and
fresh. No FPL account writes were performed by the collector.

The enabled VM timer triggered `c71e597` automatically at 10:55:45 UTC. That
run completed successfully at 11:05:47 UTC, publishing league 58005 at
10:57:49 UTC (1,218 managers) and league 131997 at 11:05:43 UTC (2,624 managers).
The elapsed 11:00 calendar occurrence then triggered a serial follow-up run;
there was no overlapping collector process. The legacy Cloud Scheduler and
Cloud Run job named `fpl-live-league-refresh` were both deleted after this
successful scheduled validation. Snapshot objects and the recovery backup were
retained. No replacement Cloud Scheduler was created.

Production monitor [run 33865448229](https://github.com/lordirfan99/fpl/actions/runs/33865448229)
passed after recovery. The timer retains its UTC half-hour window plus a
one-minute activation/reboot trigger (with up to 60 seconds random delay).

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
| api | `v2026.09.04-vm-live-refresh` (`3afe2ed`) | Cloud Build `c1c88be1-c67c-4d89-94aa-4fcba8c68fca` succeeded; health and both league status checks passed |
| live collector | `v2026.09.04-recovered-runtime` (`c71e597`) | Automatic run published both leagues and completed successfully at 11:05:47 UTC on 2026-09-04 |
| engine + bot | recovered machine image `fpl-zone-recovery-20260904` | Services active after restore; exact source commit not independently established |
| Telegram bot token | rotated 2026-09-02, in VM `config/credentials.env` only | `@Fplnaf_bot`, chat `-1004464574417` |

Update this table on every release.

## Historical freeze / unfreeze (not current deployment commands)

The commands in this section predate the zone recovery and scheduler migration.
Do not execute them verbatim: the active zone is now `us-central1-a`, and the
legacy Cloud Scheduler jobs are retired. Inventory current VM timers and GitHub
workflows before freezing. For the new public league collector, disable/stop
`fpl-live-refresh.timer` and stop `fpl-live-refresh.service` on the active VM.

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
