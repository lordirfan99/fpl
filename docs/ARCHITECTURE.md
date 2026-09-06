# Architecture

```
                 FPL public API (fantasy.premierleague.com)
                          │
          ┌───────────────┼────────────────────────────┐
          ▼                                            ▼
  engine/ (GCP VM, systemd)                    api/ (same VM, read-only)
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
- **Scheduled publishers are VM timers or bounded GitHub Actions.** They publish
  artifacts to GCS; none POSTs into the read-only API or uses Cloud Scheduler.

## Known weak spots (carry forward)

- **`data_checked` gap:** Fri deadline → Sun/Mon there is no finalized snapshot, so
  the API falls back to a ~6s live path and the engine runs `lineup_only_safe`
  (transfers locked, XI/captain only). Not a bug — a data-availability window.
- **Shared VM:** `instance-20260412-121200` also runs SportMania. Resource contention
  and blast radius. Keep memory limits, swap and external heartbeat checks active.
- **Telegram network flakiness** from GCP → api.telegram.org. All send paths must
  retry once and never crash the caller.

## GCP (`irfan-374115`, us-central1)

| Resource | Name |
|---|---|
| Cloud Run | no services or jobs; retired 6 September 2026 |
| Background jobs | Fixture/journal/finalization/monitor: GitHub Actions. Live leagues and account checks: VM systemd timers. |
| Cloud Scheduler | no jobs; do not recreate |
| GCS | `irfan-374115-fpl-snapshots` |
| VM | `instance-20260412-121200` (us-central1-a) — API + engine + bot via systemd |
| Netlify | `fpl-scout-intelligence.netlify.app` |
