import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "infra" / "scripts" / "refresh_live_leagues.py"
SPEC = importlib.util.spec_from_file_location("refresh_live_leagues", SCRIPT)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def _squad() -> list[dict]:
    return [
        {
            "element": index,
            "cost": 5.0,
            "multiplier": 1 if index <= 11 else 0,
            "is_captain": index == 1,
            "is_vice_captain": index == 2,
        }
        for index in range(1, 16)
    ]


def test_collector_builds_a_complete_validated_snapshot(monkeypatch) -> None:
    rows = [
        {"entry": 1, "entry_name": "One", "player_name": "Manager One", "event_total": 40, "total": 100, "rank": 1},
        {"entry": 2, "entry_name": "Two", "player_name": "Manager Two", "event_total": 30, "total": 90, "rank": 2},
    ]
    monkeypatch.setattr(collector.live_fpl, "current_gameweek", lambda: 2)
    monkeypatch.setattr(collector.live_fpl, "league_standings", lambda league_id: {"managers": rows, "count": 2, "pages_fetched": 1})

    def hydrate(targets, gameweek, limit):
        assert gameweek == 2 and limit == 2
        for index, row in enumerate(targets):
            row["_live_squad"] = _squad()
            row["_live_captain"] = "Captain"
            row["_live_bank"] = 5 + index
            row["_live_overall_rank"] = 100000 + index
            row["_live_event_transfers"] = index
        return 2

    monkeypatch.setattr(collector.live_fpl, "hydrate_manager_squads", hydrate)

    snapshot = collector.collect(58005)

    assert snapshot["status"] == "complete"
    assert snapshot["schema_version"] == 2
    assert snapshot["expected_count"] == snapshot["hydrated_count"] == 2
    assert len(snapshot["managers"]) == 2
    assert snapshot["managers"][0]["gw_bank"] == 5
    assert snapshot["managers"][0]["overall_rank"] == 100000
    assert snapshot["managers"][1]["transfers_made"] == 1
    assert snapshot["rank_provenance"] == "official-entry-history"
    assert snapshot["bank_provenance"] == "official-entry-history"


def test_collector_falls_back_to_league_rank_when_entry_history_absent(monkeypatch) -> None:
    rows = [{"entry": 1, "entry_name": "One", "player_name": "M1", "event_total": 40, "total": 100, "rank": 7}]
    monkeypatch.setattr(collector.live_fpl, "current_gameweek", lambda: 2)
    monkeypatch.setattr(collector.live_fpl, "league_standings", lambda lid: {"managers": rows, "count": 1, "pages_fetched": 1})

    def hydrate(targets, gameweek, limit):
        for row in targets:
            row["_live_squad"] = _squad()
            row["_live_captain"] = "C"
        return 1

    monkeypatch.setattr(collector.live_fpl, "hydrate_manager_squads", hydrate)
    snapshot = collector.collect(58005)
    assert snapshot["managers"][0]["overall_rank"] == 7  # league-rank fallback
    assert snapshot["managers"][0]["gw_bank"] is None
    assert snapshot["rank_provenance"] == "classic-league-rank-fallback"
    assert snapshot["bank_provenance"] == "unavailable"


def test_collector_rejects_partial_hydration(monkeypatch) -> None:
    monkeypatch.setattr(collector.live_fpl, "current_gameweek", lambda: 2)
    monkeypatch.setattr(collector.live_fpl, "league_standings", lambda league_id: {"managers": [{"entry": 1}], "count": 1, "pages_fetched": 1})
    monkeypatch.setattr(collector.live_fpl, "hydrate_manager_squads", lambda rows, gameweek, limit: 0)

    with pytest.raises(RuntimeError, match="hydrated 0/1"):
        collector.collect(58005)


def test_bench_boost_does_not_block_publication():
    squad = _squad()
    for pick in squad:
        pick["multiplier"] = 1
    collector._validate([{"entry_id": 1, "squad": squad}], 1)


@pytest.mark.parametrize("count", [0, 10, 12, 14])
def test_incomplete_scoring_lineup_is_rejected(count):
    squad = _squad()
    for index, pick in enumerate(squad):
        pick["multiplier"] = int(index < count)
    with pytest.raises(RuntimeError, match="invalid lineup"):
        collector._validate([{"entry_id": 1, "squad": squad}], 1)
