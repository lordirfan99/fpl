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
- [ ] `deliver_stdout` / equivalents never fail the unit on a delivery error
- [ ] CI wired as a *required* status check (not just present)
- [ ] Engine run-lock (flock) so overlapping runs can't race `data/processed/*`
- [ ] Decouple "GCS publish failed" from "no league context" (don't force `lineup_only_safe` on a push 404)
- [ ] Resolve autopilot draft PR #7 (wildcard rebuild) — the abort guard is inverted; fix + test or drop

### Phase D — cutover (after GW3 / post-Thursday)
- [ ] Point scout `deploy-api.yml` equivalent at `fpl/api` (or new workflow), deploy from a tag
- [ ] Repoint Netlify build to `fpl/web`
- [ ] Redeploy VM `engine/` + `bot/` from a tag; re-enable timers one at a time
- [ ] Resume Cloud Scheduler jobs one at a time, watch one cycle each
- [ ] Delete orphaned `fpl-scout-dashboard` Cloud Run service
- [ ] Archive `fpl-league-58005-scout` and `fpl-autopilot` (Settings → Archive)
- [ ] Update `docs/RUNBOOK.md` *Last known-good*

## Do NOT migrate
`config/credentials.env`, `config/settings.json` (chat/user ids), `*session*.json`,
`data/**`, `.venv/`, `node_modules/`, any `.backup-*`. Commit `*.example` stubs instead.
