from pathlib import Path


def test_retired_cloud_run_configs_cannot_be_reapplied():
    infra = Path(__file__).resolve().parents[2] / "infra"
    retired = (
        infra / "cloudbuild.api.yaml",
        infra / "cloudbuild.jobs.yaml",
        infra / "scripts/provision_live_refresh_infra.ps1",
        infra / "scripts/provision_scheduled_tasks.ps1",
        infra / "deploy/CUTOVER.md",
    )
    assert not any(path.exists() for path in retired)
