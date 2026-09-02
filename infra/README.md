# infra/

| Path | What |
|---|---|
| `cloudbuild.api.yaml` | Cloud Build → Cloud Run deploy for `api/` (needs monorepo path updates — Phase D) |
| `netlify.toml`, `vercel.json` | dashboard host config for `web/` |
| `deploy/gcp/` | VM systemd units + install/watchdog scripts for `engine/` + `bot/` |
| `scripts/` | scheduled-task runners + one-time GCP provisioning (`provision_*.ps1`) |
| `legacy-workflows/` | the old scout GitHub Actions (deploy + 4 refresh crons). **Reference only** — rewrite against monorepo paths before re-enabling (`docs/MIGRATION.md` Phase D) |

Runtime behaviour changes go here in the same PR as the code change (see `AGENTS.md`).
