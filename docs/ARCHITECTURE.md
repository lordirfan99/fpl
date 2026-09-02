# Architecture

```
                 FPL public API (fantasy.premierleague.com)
                          │
          ┌───────────────┼────────────────────────────┐
          ▼                                            ▼
  engine/ (GCP VM, systemd)                    api/ (Cloud Run, read-only)
  - fetch bootstrap/fixtures/my-team           - serves snapshot-backed JSON
  - competitive V4 projection (odds-free)      - /v1/*  league, team, recommendations
  - 3-GW horizon MILP optimizer                - /v1/decision/current (executable:false)
  - builds pending_plan.json                   - GCS snapshot bucket as store
          │                                            ▲
          ▼                                            │ publishes snapshots
  bot/ (Telegram @Fplnaf_bot)                          │
  - sends approval card (XI/captain/transfers)         │
  - owner taps Approve → validates plan hash → ONE FPL write
  - owner taps Reject  → nothing                       │
                                                       ▼
                                              web/ (Next.js, Netlify)
                                              - read-only dashboard
                                              - talks only to api/
```

## Principles

- **The dashboard and API never write to FPL.** `execution_authority: manual_fpl`,
  `writes_enabled: false`, enforced in `api/` and asserted in CI.
- **Only the bot writes**, and only after the owner approves a specific
  `plan_id` (canonical hash of the exact plan). No standing automation submits.
- **The engine is advisory.** It produces `pending_plan.json`; a human decision
  turns it into an FPL action.
- **Snapshots are the store.** Finalized per-GW league data lands in GCS
  (`irfan-374115-fpl-snapshots`); the API serves from there. Live/in-progress data
  is a separate, slower path and never used for journal/audit.

## Known weak spots (carry forward)

- **`data_checked` gap:** Fri deadline → Sun/Mon there is no finalized snapshot, so
  the API falls back to a ~6s live path and the engine runs `lineup_only_safe`
  (transfers locked, XI/captain only). Not a bug — a data-availability window.
- **Shared VM:** `instance-20260412-121200` also runs SportMania. Resource contention
  and blast radius. Long-term: dedicated instance or Cloud Run Job for the engine.
- **Telegram network flakiness** from GCP → api.telegram.org. All send paths must
  retry once and never crash the caller.

## GCP (`irfan-374115`, us-central1)

| Resource | Name |
|---|---|
| Cloud Run svc | `fpl-scout-api` · `fpl-scout-dashboard` (orphaned) · `fpl-scheduled-tasks` |
| Cloud Run jobs | `fpl-refresh-fixtures` `fpl-refresh-gameweek` `fpl-capture-journal` `fpl-monitor` `fpl-live-league-refresh` `fpl-decision-refresh` `fpl-decision-final-window` |
| Cloud Scheduler | one trigger per job above (all **PAUSED** during migration) |
| GCS | `irfan-374115-fpl-snapshots` |
| VM | `instance-20260412-121200` (us-central1-f) — engine + bot via systemd |
| Netlify | `fpl-scout-intelligence.netlify.app` |
