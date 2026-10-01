import copy
import json
from unittest.mock import Mock, patch

import pytest
from dashboard_packet import account_fingerprint, make_packet, private_bucket
from dashboard_account_check import check
from fpl_auto import should_generate_dashboard_preview, should_generate_plan
from pre_deadline_run import publish_dashboard_plan


def account():
    return {"picks": [{"element": i, "position": i, "selling_price": 50, "is_captain": i == 1,
                       "is_vice_captain": i == 2} for i in range(1, 16)],
            "transfers": {"bank": 10, "limit": 2, "made": 0}, "chips": []}


@pytest.mark.parametrize("config", [{"private_bucket": "public"}, {"private_bucket": "public", "public_snapshot_bucket": "public"}])
def test_missing_or_same_public_bucket_fails_before_upload(tmp_path, config):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/dashboard.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="Distinct"):
        private_bucket(tmp_path)


@pytest.mark.parametrize("field,value", [("bank", 20), ("limit", 1), ("made", 1)])
def test_account_fingerprint_tracks_budget_and_transfers(field, value):
    team = account()
    before = account_fingerprint(team)
    team["transfers"][field] = value
    assert before != account_fingerprint(team)


def test_picks_order_independent_but_lineup_prices_chips_not():
    team = account()
    before = account_fingerprint(team)
    team["picks"].reverse()
    assert before == account_fingerprint(team)
    team["picks"][0]["selling_price"] += 1
    assert before != account_fingerprint(team)
    team = account()
    team["chips"] = [{"name": "wildcard", "status_for_entry": "active", "played_by_entry": [3]}]
    assert before != account_fingerprint(team)


@pytest.mark.parametrize("chip", [None, "wildcard", "freehit"])
def test_packet_is_sanitized_canonical_and_does_not_mutate(chip):
    team = account()
    players = [{"id": i, "name": f"P{i}", "position": "MID", "secret": "sentinel"} for i in range(1, 16)]
    plan = {"plan_id": "original", "gw": 3, "team_id": 2797967, "generated_at": "now", "deadline": "later",
            "chip": chip, "target_starters": players[:11], "bench": players[11:], "transfers": [],
            "captain": {"id": 1}, "vice": {"id": 2}, "secret": "sentinel",
            "decision_summary": {"source_manifest": {"status": "ready"}, "recommended_action": "ROLL"}}
    original = copy.deepcopy(plan)
    packet = make_packet(plan, team, players, {"elements": players}, [])
    assert plan == original
    assert packet["plan_id"] == "original"
    assert packet["chip"] == chip
    assert packet["writes_enabled"] is False
    assert "sentinel" not in json.dumps(packet)
    assert len(packet["starters"] + packet["bench"]) == 15


def test_packet_projects_the_multi_gw_roadmap_and_allowlists_it():
    team = account()
    players = [{"id": i, "name": f"P{i}", "position": "MID"} for i in range(1, 16)]
    plan = {"plan_id": "p", "gw": 3, "team_id": 2797967, "generated_at": "now", "deadline": "later",
            "chip": None, "target_starters": players[:11], "bench": players[11:], "transfers": [],
            "captain": {"id": 1}, "vice": {"id": 2},
            "decision_summary": {"source_manifest": {"status": "ready"}, "recommended_action": "ROLL",
                                 "roadmap": [
                                     {"gw": 3, "action": "ROLL", "status": "recommended", "formation": "3-4-3",
                                      "bank_after": 0.5, "free_transfers_after": 1, "mean_points_with_captain": 55.0,
                                      "robust_points_with_captain": 48.0, "route": None, "secret": "sentinel"},
                                     {"gw": 4, "action": "TRANSFER", "status": "conditional", "formation": "3-4-3",
                                      "bank_after": 0.1, "free_transfers_after": 1, "mean_points_with_captain": 57.0,
                                      "robust_points_with_captain": 49.0,
                                      "route": {"moves": [{"out": "P5", "in": "P16", "hit": False, "secret": "sentinel"}]}},
                                 ]}}
    packet = make_packet(plan, team, players, {"elements": players}, [])
    assert "sentinel" not in json.dumps(packet)
    assert [week["gw"] for week in packet["roadmap"]] == [3, 4]
    assert packet["roadmap"][0]["moves"] == []
    assert packet["roadmap"][1]["moves"] == [{"out": "P5", "in": "P16", "hit": False}]
    assert set(packet["roadmap"][1]) == {
        "gw", "action", "status", "formation", "bank_after", "free_transfers_after",
        "mean_points_with_captain", "robust_points_with_captain", "moves",
    }


def test_check_failure_publishes_invalidation_without_other_client_calls(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/settings.json").write_text('{"team_id":2797967}')
    client = Mock()
    client.my_team.side_effect = RuntimeError("private token sentinel")
    with patch("dashboard_account_check.publish") as publish:
        assert check(tmp_path, client) is False
        payload = publish.call_args.args[2]
        assert payload["verified"] is False
        assert "sentinel" not in json.dumps(payload)
    assert [call[0] for call in client.mock_calls] == ["my_team"]


def test_account_check_binds_to_non_executable_dashboard_plan(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/settings.json").write_text('{"team_id":2797967}')
    (tmp_path / "data/processed").mkdir(parents=True)
    (tmp_path / "data/processed/dashboard_plan.json").write_text('{"plan_id":"preview-plan"}')
    client = Mock()
    client.my_team.return_value = account()

    with patch("dashboard_account_check.publish") as publish:
        assert check(tmp_path, client) is True
        payload = publish.call_args.args[2]
        assert payload["verified"] is True
        assert payload["plan_id"] == "preview-plan"


@pytest.mark.parametrize("hours", [26, 72, 167.9])
def test_dashboard_preview_runs_before_decision_window(hours):
    assert should_generate_dashboard_preview(hours) is True
    assert should_generate_plan({}, 4, hours) is False


@pytest.mark.parametrize("hours", [25.9, 168, 240])
def test_dashboard_preview_never_overlaps_decision_or_distant_windows(hours):
    assert should_generate_dashboard_preview(hours) is False


@pytest.mark.parametrize("outcome", [False, RuntimeError("secret sentinel")])
def test_preview_publication_failure_is_not_success(tmp_path, outcome, capsys):
    with patch("dashboard_packet.export_plan", return_value=outcome) as export:
        if isinstance(outcome, Exception):
            export.side_effect = outcome
        with pytest.raises(RuntimeError, match="preview was not published"):
            publish_dashboard_plan(tmp_path, {}, {}, [], {}, [], required=True)
    assert not (tmp_path / "data/processed/dashboard_plan.json").exists()
    assert "sentinel" not in capsys.readouterr().out


def test_optional_dashboard_failure_does_not_block_telegram_plan(tmp_path):
    with patch("dashboard_packet.export_plan", side_effect=RuntimeError):
        assert publish_dashboard_plan(tmp_path, {}, {}, [], {}, []) is False


def test_successful_publication_binds_account_check_without_touching_approval(tmp_path):
    processed = tmp_path / "data/processed"
    processed.mkdir(parents=True)
    pending = processed / "pending_plan.json"
    pending.write_text('{"plan_id":"approval"}')
    with patch("dashboard_packet.export_plan", return_value=True):
        assert publish_dashboard_plan(tmp_path, {"plan_id": "preview"}, {}, [], {}, [], required=True)
    assert json.loads((processed / "dashboard_plan.json").read_text())["plan_id"] == "preview"
    assert json.loads(pending.read_text())["plan_id"] == "approval"
