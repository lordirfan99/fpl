import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "refresh_live_leagues.py"
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
        for row in targets:
            row["_live_squad"] = _squad()
            row["_live_captain"] = "Captain"
        return 2

    monkeypatch.setattr(collector.live_fpl, "hydrate_manager_squads", hydrate)

    snapshot = collector.collect(58005)

    assert snapshot["status"] == "complete"
    assert snapshot["expected_count"] == snapshot["hydrated_count"] == 2
    assert len(snapshot["managers"]) == 2


def test_collector_rejects_partial_hydration(monkeypatch) -> None:
    monkeypatch.setattr(collector.live_fpl, "current_gameweek", lambda: 2)
    monkeypatch.setattr(collector.live_fpl, "league_standings", lambda league_id: {"managers": [{"entry": 1}], "count": 1, "pages_fetched": 1})
    monkeypatch.setattr(collector.live_fpl, "hydrate_manager_squads", lambda rows, gameweek, limit: 0)

    with pytest.raises(RuntimeError, match="hydrated 0/1"):
        collector.collect(58005)
