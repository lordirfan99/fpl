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
- [ ] `api/`  ← scout `services/api/`  (drop any deploy secrets; env-only config)
- [ ] `web/`  ← scout `web-next/`
- [ ] `engine/` ← autopilot `model/` `optimizer/` `execution/` `jobs/`
- [ ] `bot/`  ← autopilot `bot/`
- [ ] `infra/` ← both `cloudbuild*.yaml`, autopilot `deploy/` + systemd units + provision scripts, scheduler defs
- [ ] `tests/` ← merge both suites; make `pytest` pass from repo root
- [ ] Secret scan every module before its PR: `git grep -nE '(AA[A-Za-z0-9_-]{30}|BEGIN .*PRIVATE KEY|xoxb-|[0-9]{8,10}:AA)'` + eyeball `config/`

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
