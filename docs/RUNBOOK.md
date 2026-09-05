# Runbook

## Decision-first dashboard — public release and private activation gate, 5 September 2026

**Release.** The decision experience was delivered through reviewed PRs
[#71](https://github.com/lordirfan99/fpl/pull/71),
[#72](https://github.com/lordirfan99/fpl/pull/72) and
[#73](https://github.com/lordirfan99/fpl/pull/73). Follow-up deployment PRs
[#74](https://github.com/lordirfan99/fpl/pull/74),
[#75](https://github.com/lordirfan99/fpl/pull/75) and
[#76](https://github.com/lordirfan99/fpl/pull/76) preserve private Cloud Run
bindings, accept legitimate official-rank gaps, and isolate memory-heavy reads.
Every PR had both required CI jobs green and no unresolved review threads before
merge. The current release tag is `v2026.09.05-decision-dashboard-3`, commit
`4fd0bd0e3ec503bd36ef6eb7796bf4432f4e1230`.

**Public production.** Netlify deploy `6a9c280196fffd65f20e46ac` (workflow
[33972062559](https://github.com/lordirfan99/fpl/actions/runs/33972062559))
serves the phone-first This Week screen. Cloud Build
`ec27d13e-12d4-4884-ab79-1ef87b40327d` deployed the tagged API; revision
`fpl-scout-api-00083-nkg` carries the private bucket/token binding and serves
100% of traffic with concurrency one and the original 512 MiB memory limit.
`/health` reports the release commit, healthy, with writes disabled.

Read-only production checks showed both complete live leagues on the decision
screen. At the verification capture KK Old Boys was 55 points behind target
(rank 924; cutoff rank 122; 1,213/1,213 squads) and Overall IFE was 68 points
behind (2,350/2,350 squads). These values are live recorded facts and will move.
Phone browser checks rendered both league choices, the gap, signed-out private
state and rival evidence with no console errors. The Plan route returned
`private, no-store`, contained no inferred "hold" advice, and labeled public
fixtures as research. API and Netlify private routes returned 401 with
`no-store` when unauthorized. The standard production monitor passed after a
deliberate concurrent full-league/recommendation test.

**Memory incident and correction.** The first concurrent verification on
revision `fpl-scout-api-00080-fpg` placed two large league reads on one
concurrency-80 instance. Cloud Run recorded 529 MiB used against 512 MiB,
terminated the instance and returned 503. Traffic was immediately rolled back
to `fpl-scout-api-00078-hr4` while PR #76 was reviewed. The corrected revision
uses concurrency one, not more memory or an always-on instance. Repeating the
same concurrent workload returned 200 for both decision contexts, both large
recommendation/decision calls and the complete monitor; the corrected revision
had no error-level logs in the verification window.

**Private activation is intentionally incomplete.** Bucket
`irfan-374115-fpl-private-dashboard` is regional `us-central1`, uniform-access,
and has public access prevention enforced; anonymous object access returned
403. The VM and API service identities have bucket-scoped roles. Secret Manager
version 2 supplies the API read token. The authorized API path currently returns
`status=unavailable` and `packet=null`, because the VM publisher has not been
installed or enabled. Production-only Netlify owner, read-token and Auth.js
base/session variables are staged but not redeployed. `AUTH_GOOGLE_ID` and
`AUTH_GOOGLE_SECRET` remain absent: creating the Google OAuth web client with
callback `https://fpl-scout-intelligence.netlify.app/api/auth/callback/google`
requires owner confirmation in Google Cloud. Do not install the VM files, enable
`fpl-dashboard-account-check.timer`, or claim owner login works until that client
is created, stored in Netlify, redeployed and tested with
`azwariirfan@gmail.com` plus a rejected non-owner session.

The existing VM remained healthy and unchanged: Telegram, dashboard bridge,
Caddy and both SportMania services were active; no failed units were reported;
`fpl-auto-runner.timer` and `fpl-live-refresh.timer` remained scheduled. No Cloud
Scheduler job, paid feed, extra VM or always-on service was created.

**Rollback.** Route API traffic to `fpl-scout-api-00078-hr4` to remove the new
API routes, or to `fpl-scout-api-00081-v4p` to retain the public release without
the private bindings. For the web, publish Netlify deploy
`6a9ae4870734954bf0c66dec` (commit `7fbc39f`) as the preceding known-good UI.
The VM needs no rollback until the private installer is actually run. Retain the
private bucket and Secret Manager versions for diagnosis; never copy their
contents into public storage. Follow `infra/PRIVATE-DASHBOARD.md` for the scoped
VM rollback after activation.

## Current planning inputs — verified release, 4 September 2026

This section supersedes the earlier freshness/account-state claims below.
Public gameweek picks and `entry_history` are league research, not a verified
current pre-deadline squad or bank. Personal planning requires authenticated
`my-team` inputs on the VM.

**Release:** [PR #69](https://github.com/lordirfan99/fpl/pull/69), tag
`v2026.09.04-current-planning-inputs`, commit
`7fbc39fdb109988a2e9c9ff6b4b3a6c78f4200ef`. PR CI was green before merge.
Cloud Build `1611e4b2-90c2-4833-8edc-ca277a8fbedc` succeeded and API `/health`
reported that commit, healthy, with writes disabled. Netlify workflow
[33889924119](https://github.com/lordirfan99/fpl/actions/runs/33889924119)
succeeded for the same commit.

The tagged `infra/deploy/install-recommendation-planner.sh` installed only
`model/competitive_v4_client.py` and `jobs/pre_deadline_run.py` on the active
us-central1-a VM. The auto-runner timer was paused for installation and restored;
no bot restart or configuration/dependency change was required. The installer
compared both installed files against the tagged checkout. Other engine/bot
files remain from the recovered image, with no independently established SHA.
The live collector remains at `392161b`; its schema did not need another deploy.

**Read-only production verification, approximately 15:35 UTC:**

- `pre_deadline_run.py --verify-inputs-only` exited 0. Its authenticated account
  read at `2026-09-04T15:34:52.152533+00:00` verified 15 players, bank and selling
  prices, and detected an already-active GW3 Wildcard. No chip was activated.
  It used league data captured at `2026-09-04T15:32:15.970374+00:00` and reported
  `plan_saved=false`, `card_sent=false`. This did not regenerate the pending plan.
- Both leagues, on both current recommendation/decision endpoints, used fresh
  `official-fpl-live` data with `status=provisional`, `stale=false` and
  `account_state_verified=false`. Public personal transfers/captains were empty.
  Explicitly pinning the old GW2 decision returned `safe_hold`, with no transfers.
- The deployed dashboard showed league research, current-account-unverified
  messaging and pending personal recommendations, with no browser console errors.
- Telegram, dashboard bridge, Caddy and both SportMania services were active;
  there were no failed systemd units. The restored auto-runner timer's next
  occurrence was 16:05 UTC (00:05 MYT on 5 September), before the GW3 deadline
  at 17:30 UTC. Existing VM scheduling was preserved; no Cloud Scheduler added.
- Production monitor/load checks
  [33890360103](https://github.com/lordirfan99/fpl/actions/runs/33890360103)
  passed, including both leagues' freshness and recommendation contracts.

**Rollback:** follow [the scoped planner procedure](../infra/RECOMMENDATION-INPUTS.md).
The original two files are retained in
`/var/backups/fpl-planner/7fbc39fdb109988a2e9c9ff6b4b3a6c78f4200ef`.
Pause the timer and ensure no planner is running before the tagged installer's
`--rollback`; restore the previously active timer only after verification.
API rollback uses the preceding tagged release, but that restores the known
stale/public-account recommendation limitations documented in PR #69.

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
  status, reason, bank_known, rank_provenance, missing_fields}`.
  `packet_status` stays orthogonal (`advisory` / `safe_hold` / `needs_refresh`)
  so every consumer version is unaffected by how fresh the inputs are (#67);
  data freshness (`fresh` / `provisional` / `stale`) lives only in
  `freshness.status`.
- Dashboard Assistant shows an explicit **Fresh / Provisional / Stale · safe
  hold** state and suppresses the transfer card on a hold.
- `monitor_production.py` fails loudly on a silently-stale recommendation;
  `safe_hold` is an accepted honest degradation.
- No model/scoring change. `fetch_competitive_v4(require_executable_plan=True)`
  and the Telegram `/approve` gate remain the only execution authority; a
  `safe_hold` / `needs_refresh` packet never satisfies the executable path.

**Local verification.** `pytest` 339 passed / 6 skipped (incl. new
`api/tests/test_recommendation_freshness.py` — fresh / stale-finalized /
incomplete-live / mixed-source / future+malformed timestamp / no-write /
Telegram-only cases); `ruff check .` clean; `web` typecheck + build clean.

**Release.** PRs #66 (`dfb9674`) + #67 (`392161b`), squash-merged to `main`.
Release tag **`v2026.09.04-recommendation-freshness-2`** (`392161b`).
- **API**: Cloud Build `b0d6241f-2117-4e1c-aaa5-3ce6c95e964b` (SUCCESS) →
  Cloud Run `fpl-scout-api` now reports revision `392161b`.
- **Collector**: `install-live-refresh.sh v2026.09.04-recommendation-freshness-2`
  on the VM (`us-central1-a`); `/opt/fpl-live-refresh/current` →
  `releases/392161b33e66f3947dd5f6f145ee1401df475beb`. One manual run completed
  `Result=success ExecMainStatus=0`, publishing schema-v2 snapshots for 58005
  (12:17:41Z, 1217 mgrs) and 131997 (12:27:14Z, 2623 mgrs); the 30-min timer
  was restarted and the temporary checkout removed.

**Production verification — 4 September 2026, ~12:30 UTC.**

| Item | Before | After |
|---|---|---|
| API revision | `3afe2ed` | `392161b` (`/health` ok) |
| `/v1/recommendations/current` source | `snapshot` (finalized) | `official-fpl-live` |
| `snapshot_at` / `data_age_hours` | `2026-09-01T14:00:07Z` / **70.0 h**, `stale=true` | `2026-09-04T12:17:41Z` / **~0.2 h**, `stale=false` |
| `freshness.status` / `packet_status` | (absent) / `advisory` | `fresh` / `advisory` |
| `freshness.bank_known` / `rank_provenance` | (absent) | `true` / `official-entry-history` |
| my `inputs.bank` | (from 70 h-old file) | `£1.0m` (live `entry_history`) |
| `/v1/decision/current` | advisory, quality `valid` | advisory, quality `valid`, `freshness.status=fresh`, `executable=false`, `writes_enabled=false` |
| `/v1/leagues/58005/live/status` | ready, gw2 | ready, gw2, age 0.21 h, 1217 mgrs |
| `/v1/leagues/131997/live/status` | ready, gw2 | ready, gw2, age 0.05 h, 2623 mgrs |
| `/v1/live/team` | gw2, 15 picks, 99 pts | unchanged (gw2, 15 picks, 99 pts) |
| Dashboard Assistant "Recommendation" chip | "Snapshot quality: Valid/Pending" | "**Fresh** — Official live snapshot · 13m ago" |
| VM `systemctl --failed` | — | empty (no failed units) |
| `entry/2797967` transfers / chips | GW1 0, GW2 1, `chips: []` | **unchanged** — no FPL write occurred |

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
| api + dashboard | `v2026.09.04-current-planning-inputs` (`7fbc39f`) | API health and Netlify deployment verified; production monitor 33890360103 passed; public league research is provisional and does not claim authenticated personal recommendations |
| VM planning client + pre-deadline job | `v2026.09.04-current-planning-inputs` (`7fbc39f`) | Two-file scoped installation; authenticated input-only verification passed, no plan saved or card sent; timer restored |
| live collector | `v2026.09.04-recommendation-freshness-2` (`392161b`) | schema-v2 (`gw_bank` + official `overall_rank` from `entry_history`); manual run `Result=success`, published 58005 (12:17:41Z) + 131997 (12:27:14Z) on 2026-09-04; 30-min timer restarted; no failed units |
| remaining engine + bot | recovered machine image `fpl-zone-recovery-20260904` | Services active; except for the two planning files above, exact source commit not independently established |
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
