from pathlib import Path


def test_job_build_config_builds_both_monorepo_images():
    config = (Path(__file__).resolve().parents[2] / "infra" / "cloudbuild.jobs.yaml").read_text(encoding="utf-8")
    assert "api/Dockerfile.tasks" in config
    assert "api/Dockerfile.live-refresh" in config
    assert "fpl-scheduled-tasks" in config
    assert "fpl-live-refresh" in config
