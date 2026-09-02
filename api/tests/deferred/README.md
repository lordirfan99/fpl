# Deferred tests

Not run by CI yet. Two reasons:

**Needs scout-root scripts** — `scripts/run_scheduled_task.py`, `fetch_gw_data_fixed`:
`test_scheduled_tasks`, `test_official_refresh`, `test_live_snapshot_collector`,
`test_snapshot_writer`. Rejoin when `infra/` lands.

**Needs bulk snapshot fixtures** — `data/bootstrap_cache.json`,
`data/gw*_league58005_data.json` (21–48 MB each), `data/journal/**`:
`test_api`, `test_artifact_integrity`, `test_contract_matrix`, `test_journal`,
`test_projection_api`. The 141 MB committed `data/` from scout is **not** migrated
(repo health + it contains other managers' data). Rejoin once a small synthetic
fixture set exists under `api/tests/fixtures/` — `docs/MIGRATION.md` Phase C.
