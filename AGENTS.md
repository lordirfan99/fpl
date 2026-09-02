# Working agreement — agents and humans

This repo drives real-money-free but real-stakes Fantasy Premier League decisions
for one owner, across a public API, a dashboard, a decision engine and a Telegram
bot on a production VM. It exists because the previous two-repo, many-branch,
two-assistant setup produced conflicting changes, straight-to-`master` commits and
live edits on the production VM — one of which broke the pipeline two days before a
Wildcard deadline (2026-09-02).

## Non-negotiable

1. **Every change is a branch + a pull request.** Never push to `main`. Branch
   names: `feat/…`, `fix/…`, `chore/…`, `docs/…`.
2. **CI must be green before merge.** `lint` + `pytest` are required checks on
   `main`. A red PR does not merge, ever.
3. **Never edit files on the VM (`/opt/fpl-*`) or in Cloud Run directly.** The flow
   is: branch → PR → CI → review → tagged release → deploy. If you need to look at
   the VM, read only. No `sed -i`, no hot patches, no `.backup-*` files next to a
   running service.
4. **One assistant at a time on a given change.** If Claude and ChatGPT/Codex both
   have context, hand off explicitly in the PR; do not both push to the same branch.
5. **Secrets never enter git.** No tokens, cookies, `credentials.env`,
   `settings.json`, service-account keys, or `data/`. `.gitignore` enforces the
   common cases; check anyway before every push.
6. **The engine cannot write to FPL.** Only the bot, only on explicit owner
   approval of a specific plan hash. Any change that touches that boundary needs a
   test proving the guard still holds.

## Deploy

- Tag `main` (`vYYYY.MM.DD` or semver) for a release. Deploy from the tag.
- `infra/` holds the exact Cloud Build / systemd / scheduler definitions. Changing
  runtime behaviour = changing `infra/` in the same PR as the code.
- Rollback = redeploy the previous tag. Keep the last known-good tag noted in
  `docs/RUNBOOK.md`.

## When something breaks

See `docs/RUNBOOK.md` § Incident. First move is always: freeze (pause schedulers,
disable write-timers), then diagnose on a branch — never on the box.
