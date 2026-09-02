from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_scheduled_task_image_uses_monorepo_paths():
    dockerfile = (ROOT / "api" / "Dockerfile.tasks").read_text(encoding="utf-8")
    assert "COPY api/requirements.txt" in dockerfile
    assert "COPY api/app" in dockerfile
    assert "COPY infra/scripts" in dockerfile
    assert "services/api" not in dockerfile


def test_live_refresh_image_uses_monorepo_paths():
    dockerfile = (ROOT / "api" / "Dockerfile.live-refresh").read_text(encoding="utf-8")
    assert "COPY api/requirements.txt" in dockerfile
    assert "COPY api/app" in dockerfile
    assert "COPY infra/scripts/refresh_live_leagues.py" in dockerfile
    assert "services/api" not in dockerfile
