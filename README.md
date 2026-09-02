# fpl — FPL Scout Intelligence

Single source of truth for Irfan's Fantasy Premier League system (2026/27 season,
team `2797967`, main league `58005`). Replaces `fpl-league-58005-scout` and
`fpl-autopilot`, both now archived read-only.

## Layout

| Path | What | Runtime |
|---|---|---|
| `api/` | Read API — FastAPI, snapshot-backed, **read-only** (`execution_authority: manual_fpl`) | Cloud Run `fpl-scout-api` (`irfan-374115`, us-central1) |
| `web/` | Dashboard — Next.js | Netlify `fpl-scout-intelligence.netlify.app` |
| `engine/` | Decision engine — competitive V4 projection, horizon MILP optimizer, scheduled jobs | GCP VM `instance-20260412-121200` (us-central1-f), systemd timers |
| `bot/` | Telegram approval bot `@Fplnaf_bot` — the only path that can trigger a real FPL write, and only via explicit owner approval | same VM, `fpl-telegram.service` |
| `infra/` | Cloud Build configs, systemd units, provisioning scripts, scheduler definitions | — |
| `docs/` | [ARCHITECTURE](docs/ARCHITECTURE.md) · [RUNBOOK](docs/RUNBOOK.md) · [MIGRATION](docs/MIGRATION.md) | — |
| `tests/` | One suite, gates every PR | GitHub Actions |

## Rules

Read [`AGENTS.md`](AGENTS.md). Short version: every change is a branch + PR, CI must
pass, nothing is edited live on the VM, `main` is the only source of truth.

## Status

🚧 Migration in progress — see [docs/MIGRATION.md](docs/MIGRATION.md). The legacy
system is **frozen** (schedulers paused, VM write-timers disabled) during the move.
