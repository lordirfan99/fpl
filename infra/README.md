# infra/

| Path | What |
|---|---|
| `netlify.toml`, `vercel.json` | dashboard host config for `web/` |
| `deploy/gcp/` | VM systemd units + tagged install/rollback scripts for API, engine and bot |
| `scripts/` | GitHub scheduled-task runners, monitors and VM helpers |
| `legacy-workflows/` | the old scout GitHub Actions (deploy + 4 refresh crons). **Reference only** — rewrite against monorepo paths before re-enabling (`docs/MIGRATION.md` Phase D) |

Cloud Run build definitions and Cloud Scheduler provisioning scripts were
removed after the 6 September 2026 VM cutover so they cannot be reapplied by
mistake. Git history remains the archive.

Runtime behaviour changes go here in the same PR as the code change (see `AGENTS.md`).
