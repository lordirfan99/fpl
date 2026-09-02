# Migration — legacy repos → `fpl` monorepo

**Method:** clean copy, no git-history merge. A public repo must not inherit a
secret buried in old history. Legacy repos are archived read-only for reference.

**Source repos**
- `lordirfan99/fpl-league-58005-scout` @ `master` (post PR #13 = PR #12 state)
- `lordirfan99/fpl-autopilot` @ `codex/gcp-deploy` (`b6ba31e`)

## Checklist

### Phase A — skeleton  ✅
- [x] Create public repo, push `main` with README / AGENTS / .gitignore / docs / CI
- [x] Branch protection on `main` + required CI check
- [ ] Verify a throwaway PR is blocked until CI passes

### Phase B — code in (one PR per module, CI green each time)
- [x] `api/`  ← scout `services/api/`  (PR #1). Ruff floor + pytest paths added.
- [x] `web/`  ← scout `web-next/`  (PR #3). typecheck + build in CI.
- [x] `engine/` + `bot/` ← autopilot `model/optimizer/execution/jobs`, `telegram_bot.py`  (PR #2)
- [x] `infra/` ← scout `cloudbuild.api.yaml`/`netlify.toml`/`scripts/`, autopilot `deploy/`; old scout workflows parked in `infra/legacy-workflows/`
- [ ] `tests/` — per-module `*/tests/` for now; a repo-root suite is Phase C
- [x] Secret scan run on every module PR. Scrubbed for the public repo:
      owner Telegram user/chat ids; `league_registry.json` + `prize_targets.json`
      (real league names + real-money prizes) → `.example` stubs only.

**Quarantined tests** (`*/tests/deferred/`), rejoin in Phase C:
- `api/`: 5 need bulk `data/` snapshot fixtures; 4 need scout-root scripts (now in `infra/scripts/`, still need fixtures)
- `engine/`: 9 need fixtures / browser deps / exact-string introspection / scrubbed-id asserts
- The 141 MB committed `data/` from scout was **not** migrated — needs a small synthetic fixture set under `*/tests/fixtures/`

### Phase C — carry-forward fixes (own PRs, not bundled)
- [ ] One `telegram_notify` helper — replace the 3 divergent senders; retry once, never raise
- [x] `deliver_stdout` / equivalents never fail the unit on a delivery error (PR #5)
- [x] CI wired as a *required* status check (Phase A — strict + both contexts)
- [x] Engine run-lock (flock) so overlapping runs can't race `data/processed/*` (PR #5)
- [x] Decouple "GCS publish failed" from "no league context" (PR #5)
- [ ] `telegram_notify` adopted in `bot/telegram_bot.py` too (PR #5 did the jobs; the bot has its own PTB path)
- [ ] Resolve autopilot draft PR #7 (wildcard rebuild) — abort guard is inverted. Blocked: the wildcard feature isn't in the monorepo yet; port + fix + test together.
- [ ] Synthetic fixture set under `*/tests/fixtures/` to un-quarantine the ~18 `deferred/` tests

### Phase D — cutover (owner-gated: after GW3 / post-Thursday)

**Decisions needed before starting:**
1. **API `data/` bootstrap.** `api/Dockerfile` currently `COPY data ./data` (the 141 MB fallback, not migrated). Options: (a) GCS-only — drop `COPY data`, API reads everything from `FPL_SNAPSHOT_BUCKET`; (b) build step syncs a minimal bootstrap from GCS into the image. Pick before rewriting `infra/cloudbuild.api.yaml`.
2. **Artifact Registry repo** — keep `_REPOSITORY: fpl-scout` or rename to `fpl`.
3. **VM deploy mechanism** — there is no automated one today (manual file copy per the old deploy contract). Add `infra/deploy/vm-sync.sh` (checkout tag → back up → rsync `engine/`+`bot/`+`infra/deploy` to `/opt/fpl-autopilot` → restart units → verify), or keep manual.

**Steps (each reversible until the archive):**
- [ ] Rewrite `api/Dockerfile*` + `infra/cloudbuild.api.yaml` for monorepo paths (`api/…`), per decision 1
- [ ] Add `.github/workflows/deploy-api.yml` (adapt `infra/legacy-workflows/deploy-api.yml`: `branches: [main]`, `paths: api/** infra/cloudbuild.api.yaml`, `--config infra/cloudbuild.api.yaml`, keep the verify block)
- [ ] Set repo vars on `fpl`: `GCP_PROJECT_ID`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOY_SERVICE_ACCOUNT` (+ a `production` environment)
- [ ] Tag `v2026.09.XX`; run the deploy workflow; confirm `/health` `revision` == tag SHA
- [ ] Netlify: relink the site from `fpl-league-58005-scout` to `fpl`, base `web/` (UI action); redeploy; check the dashboard
- [ ] VM: `vm-sync.sh` (or manual) from the tag; `systemctl enable --now` the write-timers one at a time
- [ ] `gcloud scheduler jobs resume …` — one job, watch one cycle, then the next
- [ ] `gcloud run services delete fpl-scout-dashboard --region us-central1 --project irfan-374115`
- [ ] Archive `fpl-league-58005-scout` and `fpl-autopilot` (Settings → Archive) — **do this last**
- [ ] Update `docs/RUNBOOK.md` *Last known-good* table

## Do NOT migrate
`config/credentials.env`, `config/settings.json` (chat/user ids), `*session*.json`,
`data/**`, `.venv/`, `node_modules/`, any `.backup-*`. Commit `*.example` stubs instead.
