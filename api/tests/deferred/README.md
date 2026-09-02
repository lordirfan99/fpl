# Deferred tests

These test the scheduled-task / snapshot-writer scripts that lived at the
**scout repo root** (`scripts/run_scheduled_task.py`, `fetch_gw_data_fixed`, …),
not inside `services/api/`. They rejoin the suite when `infra/` lands and those
scripts have a home. Tracked in `docs/MIGRATION.md` Phase B (`infra/`).
