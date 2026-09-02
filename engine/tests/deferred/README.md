# Deferred engine tests

Not run by CI yet — quarantined during the monorepo migration (Phase B).
They fail for one of: needs bulk `data/` fixtures, needs the browser-login
deps (`camoufox`/`playwright`), does source-introspection on exact strings
that shifted, or asserts against the owner Telegram ids that were scrubbed
for the public repo.

Rejoin in Phase C (`docs/MIGRATION.md`) — needs a small synthetic fixture
set + a decision on the project-root / config strategy. Do NOT delete;
each is real coverage of the approval-binding / odds-gate / security paths.
