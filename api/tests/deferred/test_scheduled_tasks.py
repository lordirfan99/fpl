import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "run_scheduled_task.py"
SPEC = importlib.util.spec_from_file_location("run_scheduled_task", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

client = TestClient(app)


def test_latest_final_gameweek_picks_highest_finished_and_data_checked(monkeypatch) -> None:
    events = [
        {"id": 1, "finished": True, "data_checked": True},
        {"id": 2, "finished": True, "data_checked": True},
        {"id": 3, "finished": True, "data_checked": False},  # played, bonus not confirmed
        {"id": 4, "finished": False, "data_checked": False},
    ]
    monkeypatch.setattr(MODULE, "_bootstrap_events", lambda: events)
    assert MODULE._latest_final_gameweek() == 2


def test_latest_final_gameweek_is_zero_before_any_gameweek_completes(monkeypatch) -> None:
    monkeypatch.setattr(MODULE, "_bootstrap_events", lambda: [{"id": 1, "finished": False, "data_checked": False}])
    assert MODULE._latest_final_gameweek() == 0


def test_task_names_match_the_provisioned_cloud_run_jobs() -> None:
    parser_choices = {"fixtures", "capture-journal", "finalize-gameweek", "monitor"}
    # cloudbuild.api.yaml deploys one job per task arg.
    cloudbuild = (Path(__file__).resolve().parents[3] / "cloudbuild.api.yaml").read_text(encoding="utf-8")
    for task in parser_choices:
        assert f"--args={task}" in cloudbuild, task


def test_journal_export_still_serves_the_packaged_csv() -> None:
    response = client.get("/v1/journal/2026-27/export?filename=gameweeks.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "gameweek" in response.text.splitlines()[0].lower()


def test_unknown_journal_export_is_rejected() -> None:
    assert client.get("/v1/journal/2026-27/export?filename=secrets.json").status_code == 400
