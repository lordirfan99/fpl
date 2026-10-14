import json

import pytest
from fastapi.testclient import TestClient

from app.compact_league import compact_managers
from app.schemas import ManagerSummary, Pick
from app.repository import SnapshotNotFoundError


def manager(entry_id):
    return {"entry_id": entry_id, "entry_name": "Test FC", "player_name": "Test Manager",
            "gw_points": 50, "total_points": 300, "rank": 10000, "league_rank": entry_id,
            "squad_cost": 100.5, "captain": "Player 1", "gw_transfers": 1,
            "transfer_details": [{"out": "Old", "in": "New", "history": [0] * 100}],
            "chips_used": [{"name": "wildcard", "time": "recorded"}], "league_last_rank": 5,
            "unused_history": [0] * 1000,
            "squad": [{"element": i, "name": f"Player {i}", "position": "MID", "team": "Club",
                       "cost": 5.0, "multiplier": 2 if i == 1 else 1 if i < 12 else 0,
                       "is_captain": i == 1, "is_vice_captain": i == 2, "selected_by": 10.0}
                      for i in range(1, 16)]}


def test_encoding_preserves_every_manager_pick_order_flags_and_recorded_cost():
    raw = [manager(1), manager(2)]
    raw[1]["squad"][0]["cost"] = 5.5
    encoded = compact_managers(raw)
    assert len(encoded["managers"]) == 2
    assert len(encoded["players"]) == 16
    for original, row in zip(raw, encoded["managers"]):
        squad = [{**encoded["players"][index], "multiplier": multiplier, "is_captain": captain,
                  "is_vice_captain": vice} for index, multiplier, captain, vice in row["squad"]]
        assert squad == [Pick.model_validate(pick).model_dump() for pick in original["squad"]]
        assert {key: row[key] for key in ManagerSummary.model_fields} == ManagerSummary.model_validate(original).model_dump()
        assert row["transfer_details"] == [{"out": "Old", "in": "New"}]
        assert row["chips_used"] == [{"name": "wildcard"}]
        assert row["league_last_rank"] == 5
        assert "unused_history" not in row


def test_large_population_is_preserved_with_bounded_wire_size():
    raw = [manager(index) for index in range(1, 1194)]
    encoded = compact_managers(raw)
    assert len(encoded["managers"]) == 1193
    assert len(encoded["players"]) == 15
    assert len(json.dumps(encoded).encode()) < 1_000_000
    assert len(json.dumps(encoded)) < len(json.dumps(raw)) / 5


@pytest.mark.parametrize("broken", ["partial", "duplicate"])
def test_incomplete_or_duplicate_squads_fail_closed(broken):
    raw = [manager(1), manager(2)]
    if broken == "partial":
        raw[0]["squad"].pop()
    else:
        raw[1]["entry_id"] = 1
    with pytest.raises(ValueError):
        compact_managers(raw)


def valid_manager(entry_id):
    row = manager(entry_id)
    positions = ["GKP", *(["DEF"] * 4), *(["MID"] * 4), "FWD", "FWD", "GKP", "DEF", "MID", "FWD"]
    for pick, position in zip(row["squad"], positions):
        pick["position"] = position
    return row


def test_finalized_compact_preserves_identity_and_full_population(monkeypatch):
    import app.main as main
    rows = [valid_manager(1), valid_manager(2)]
    snapshot = {"gw": 6, "total_entries": 2, "competitors": rows, "fetched_at": "2026-10-01T08:00:00Z",
                "_artifact_sha256": "a" * 64}
    monkeypatch.setattr(main, "_league_or_404", lambda *_: snapshot)
    response = TestClient(main.app).get("/v1/leagues/58005/compact?gw=6")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == body["declared_count"] == len(body["managers"]) == 2
    assert body["gameweek"] == 6
    assert body["meta"]["data_hash"] == "a" * 64
    assert body["meta"]["snapshot_at"].startswith("2026-10-01T08:00:00")
    assert body["encoding"] == "league-squads-v1"
    assert len(body["players"]) == 15
    snapshot["total_entries"] = 3
    assert TestClient(main.app).get("/v1/leagues/58005/compact?gw=6").status_code == 409


def test_live_compact_keeps_bench_boost_scoring_without_fabricating_chip(monkeypatch):
    import app.main as main
    row = valid_manager(1)
    for pick in row["squad"]:
        pick["multiplier"] = 2 if pick["is_captain"] else 1
    snapshot = {"managers": [row], "expected_count": 1, "gameweek": 7, "captured_at": "2026-10-01T09:00:00Z"}
    monkeypatch.setattr(main.repository, "live_league", lambda _: snapshot)
    response = TestClient(main.app).get("/v1/leagues/58005/live/compact")
    assert response.status_code == 200
    body = response.json()
    assert body["provisional"] is True
    assert body["meta"]["generated_at"] == snapshot["captured_at"]
    assert all(ref[1] > 0 for ref in body["managers"][0]["squad"])
    assert "active_chip" not in body["managers"][0]


def test_missing_public_reference_is_explicit_unavailable(monkeypatch):
    import app.main as main
    def missing():
        raise SnapshotNotFoundError("bootstrap_cache.json")
    monkeypatch.setattr(main.repository, "bootstrap", missing)
    response = TestClient(main.app).get("/v1/catalog/compact")
    assert response.status_code == 503
    assert response.json() == {"detail": "public_snapshot_unavailable"}
