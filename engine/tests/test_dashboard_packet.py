import copy
import json
from unittest.mock import Mock, patch

import pytest
from dashboard_packet import account_fingerprint, make_packet, private_bucket
from dashboard_account_check import check


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
