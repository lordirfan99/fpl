import datetime
import json
from unittest.mock import Mock, patch

import pytest

import fpl_auto
from dashboard_packet import PacketValidationError, publish
from pipeline_health import (PublicationError, diagnostic_from_stderr, failure_episode,
                             storage_action)
from pre_deadline_run import publish_dashboard_plan


def test_unverified_source_is_explained_without_weakening_publication_guard(tmp_path):
    plan = {"plan_id": "pending", "gw": 7, "run_id": "private sentinel",
            "decision_summary": {"source_manifest": {
                "status": "lineup_only_safe", "refresh_failures": ["private sentinel"]}}}
    with patch("dashboard_packet.private_bucket", return_value="configured"):
        with pytest.raises(PublicationError, match="code=source_not_ready") as caught:
            publish_dashboard_plan(tmp_path, plan, {}, [], {}, [], required=True)
    assert "sentinel" not in str(caught.value)
    status = json.loads((tmp_path / "data/processed/dashboard_publication_status.json").read_text())
    assert status["code"] == "source_not_ready"
    assert status["refresh_failure_count"] == 1
    assert len(status["run_id_hash"]) == 64
    assert "sentinel" not in json.dumps(status)
    assert not (tmp_path / "data/processed/dashboard_plan.json").exists()


@pytest.mark.parametrize("stage", ["client", "metadata", "upload"])
def test_storage_exception_keeps_the_phase_but_not_upstream_private_message(stage):
    with pytest.raises(PublicationError) as caught:
        storage_action(stage, Mock(side_effect=PermissionError("Bearer private sentinel")))
    parsed = diagnostic_from_stderr(str(caught.value))
    assert parsed == {"code": f"storage_{stage}_failed", "stage": stage, "error_class": "PermissionError"}
    assert "sentinel" not in str(caught.value)


def test_privacy_guard_still_prevents_upload():
    bucket = Mock()
    bucket.iam_configuration.public_access_prevention = "inherited"
    with patch("dashboard_packet.private_bucket", return_value="configured"):
        with patch("dashboard_packet.storage_action", side_effect=lambda stage, action: bucket if stage == "client" else None):
            with pytest.raises(PacketValidationError) as caught:
                publish("unused", "plan", {})
    assert caught.value.code == "privacy_guard_failed"
    bucket.blob.assert_not_called()


def test_local_binding_error_survives_diagnostic_write_failure(tmp_path):
    with patch("dashboard_packet.export_plan", return_value=True):
        with patch("pre_deadline_run.atomic_write_json", side_effect=PermissionError("sentinel")):
            with pytest.raises(PublicationError, match="code=local_binding_failed") as caught:
                publish_dashboard_plan(tmp_path, {"gw": 7}, {}, [], {}, [], required=True)
    assert "sentinel" not in str(caught.value)


def test_success_records_health_and_preserves_pending_plan(tmp_path):
    processed = tmp_path / "data/processed"
    processed.mkdir(parents=True)
    pending = processed / "pending_plan.json"
    pending.write_bytes(b'old approved identity')
    with patch("dashboard_packet.export_plan", return_value=True):
        assert publish_dashboard_plan(tmp_path, {"gw": 7}, {}, [], {}, [], required=True)
    assert pending.read_bytes() == b'old approved identity'
    assert json.loads((processed / "dashboard_publication_status.json").read_text())["status"] == "published"


def test_failure_identity_survives_restart_and_reminds_only_after_24h():
    state = {}
    now = datetime.datetime(2026, 10, 15, tzinfo=datetime.timezone.utc)
    diagnostic = {"code": "source_not_ready", "stage": "export", "error_class": "PacketValidationError"}
    kwargs = {"gameweek": 7, "mode": "preview", "rc": 1}
    assert failure_episode(state, "failure", diagnostic, now, **kwargs)
    state = json.loads(json.dumps(state))  # A fresh scheduled process loads persisted state.
    assert not failure_episode(state, "failure", diagnostic, now + datetime.timedelta(hours=2), **kwargs)
    assert state["failure"]["count"] == 2
    assert failure_episode(state, "failure", diagnostic, now + datetime.timedelta(hours=24), **kwargs)
    assert failure_episode(state, "failure", diagnostic, now + datetime.timedelta(hours=25),
                           **dict(kwargs, mode="plan"))  # Deadline decisions get a new alert.
    assert failure_episode(state, "failure", dict(diagnostic, error_class="PermissionError"),
                           now + datetime.timedelta(hours=26), **kwargs)


def test_arbitrary_traceback_does_not_leak_messages_and_different_errors_are_distinct():
    parsed = diagnostic_from_stderr('Traceback...\nPermissionError: private token sentinel')
    assert parsed["error_class"] == "PermissionError"
    assert "sentinel" not in json.dumps(parsed)
    assert parsed != diagnostic_from_stderr('RuntimeError: another private message')


def test_scheduled_retries_continue_while_duplicate_stdout_alerts_stop(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.json").write_text(json.dumps({"league_intelligence": {"enabled": True}}))
    monkeypatch.setattr(fpl_auto, "BASE", str(tmp_path))
    monkeypatch.setattr(fpl_auto, "STATE_FILE", str(tmp_path / "data/processed/auto_state.json"))
    monkeypatch.setattr(fpl_auto, "LEAGUE_STATE_FILE", str(tmp_path / "league.json"))
    deadline = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=72)
    monkeypatch.setattr(fpl_auto, "fetch", lambda url: {"events": [
        {"id": 7, "finished": False, "is_next": True, "deadline_time": deadline.isoformat()}]})
    calls = []
    failing = [True]

    def run(script, args=None, env=None):
        calls.append((script, args))
        if script == "league_intelligence.py" and failing[0]:
            return 1, "private stdout sentinel", "PermissionError: private stderr sentinel"
        if script == "pre_deadline_run.py" and failing[0]:
            return 1, "private sentinel", str(PublicationError("source_not_ready", "export", "PacketValidationError"))
        return 0, "", ""

    monkeypatch.setattr(fpl_auto, "run", run)
    monkeypatch.setattr(fpl_auto, "send_telegram", Mock(side_effect=AssertionError("Unexpected direct delivery")))
    fpl_auto.main()
    first = capsys.readouterr()
    assert "dashboard preview unavailable" in first.out and "league intelligence unavailable" in first.out
    assert "sentinel" not in first.out
    fpl_auto.main()
    duplicate = capsys.readouterr()
    assert "unavailable" not in duplicate.out
    assert "unavailable" in duplicate.err
    assert sum(script == "pre_deadline_run.py" for script, _ in calls) == 2
    assert all(args == ["--dashboard-only", "--notifications-disabled"]
               for script, args in calls if script == "pre_deadline_run.py")
    failing[0] = False
    fpl_auto.main()
    assert capsys.readouterr().out.count("recovered") == 2
    fpl_auto.main()
    assert "recovered" not in capsys.readouterr().out
