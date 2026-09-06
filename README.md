# fpl — FPL Scout Intelligence

Single source of truth for Irfan's Fantasy Premier League system (2026/27 season,
team `2797967`, main league `58005`). Replaces `fpl-league-58005-scout` and
`fpl-autopilot`, both now archived read-only.

## Layout

| Path | What | Runtime |
|---|---|---|
| `api/` | Read API — FastAPI, snapshot-backed, **read-only** (`execution_authority: manual_fpl`) | existing GCP VM behind `sportmania.duckdns.org/fpl-scout-api` |
| `web/` | Dashboard — Next.js | Netlify `fpl-scout-intelligence.netlify.app` |
| `engine/` | Decision engine — competitive V4 projection, horizon MILP optimizer, scheduled jobs | GCP VM `instance-20260412-121200` (us-central1-a), systemd timers |
| `bot/` | Telegram approval bot `@Fplnaf_bot` — the only path that can trigger a real FPL write, and only via explicit owner approval | same VM, `fpl-telegram.service` |
| `infra/` | Tagged VM installers, systemd units and GitHub scheduled-task runners | — |
| `docs/` | [ARCHITECTURE](docs/ARCHITECTURE.md) · [RUNBOOK](docs/RUNBOOK.md) · [MIGRATION](docs/MIGRATION.md) | — |
| `tests/` | One suite, gates every PR | GitHub Actions |

## Rules

Read [`AGENTS.md`](AGENTS.md). Short version: every change is a branch + PR, CI must
pass, nothing is edited live on the VM, `main` is the only source of truth.

## Status

The dashboard, API, collector, engine and approval bot are active. Cloud Run and
Cloud Scheduler resources are retired; see [docs/RUNBOOK.md](docs/RUNBOOK.md).
