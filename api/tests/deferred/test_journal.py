from __future__ import annotations

from app.journal import build_gameweek_journal, build_index, verify_record_hash, write_immutable_record
from app.main import app
from app.repository import ArtifactIntegrityError, SnapshotRepository
from fastapi.testclient import TestClient


client = TestClient(app)


def test_public_journal_api_and_export() -> None:
    index = client.get("/v1/journal?season=2026-27")
    assert index.status_code == 200
    assert index.json()["gameweeks"][0]["gameweek"] == 1
    entry = client.get("/v1/journal/2026-27/gw/1")
    assert entry.status_code == 200
    assert entry.json()["quality"]["issues"] == ["predeadline_evidence_missing"]
    exported = client.get("/v1/journal/2026-27/export?filename=gameweeks.csv")
    assert exported.status_code == 200
    assert "gw_points" in exported.text


def test_journal_rejects_path_traversal_and_unknown_gw() -> None:
    assert client.get("/v1/journal?season=../../private").status_code == 400
    assert client.get("/v1/journal/2026-27/gw/39").status_code == 400


def test_journal_builder_is_deterministic_and_private_notes_are_not_inputs() -> None:
    snapshot = {"fetched_at": "2026-08-25T00:00:00Z", "total_entries": 1, "competitors": [{
        "entry_id": 1, "gw_points": 10, "total_points": 10, "overall_rank": 99, "league_rank": 1,
        "captain": "A", "squad": [{"element": 7, "name": "A", "team": "Club", "position": "MID",
        "multiplier": 2, "is_captain": True, "is_vice_captain": False}],
    }]}
    analysis = {"generated_at": "2026-08-25T00:00:00Z", "total_competitors": 1,
                "squad_ownership": {"avg_gw_points": 8}}
    live = {"elements": [{"id": 7, "stats": {"total_points": 5, "minutes": 90}}]}
    first = build_gameweek_journal(season="2026-27", gameweek=1, team_id=1, league_id=2,
                                   snapshot=snapshot, analysis=analysis, live=live)
    second = build_gameweek_journal(season="2026-27", gameweek=1, team_id=1, league_id=2,
                                    snapshot=snapshot, analysis=analysis, live=live)
    assert first == second
    assert first["summary"]["captain_points"] == 5
    assert "private" not in str(first).lower()
    assert build_index([first], "2026-27")["totals"]["completed"] == 1
    assert verify_record_hash(first)


def test_journal_record_cannot_be_replaced_or_silently_tampered(tmp_path) -> None:
    payload = {"season": "2026-27", "gameweek": 1, "record_hash": ""}
    from app.journal import record_hash
    payload["record_hash"] = record_hash(payload)
    path = tmp_path / "gw01.json"
    assert write_immutable_record(path, payload) is True
    assert write_immutable_record(path, payload) is False
    replacement = dict(payload); replacement["gameweek"] = 2
    replacement["record_hash"] = record_hash(replacement)
    import pytest
    with pytest.raises(FileExistsError):
        write_immutable_record(path, replacement)
    path.write_text(path.read_text(encoding="utf-8").replace('"gameweek": 1', '"gameweek": 9'), encoding="utf-8")
    with pytest.raises(ValueError):
        write_immutable_record(path, replacement)


def test_journal_index_cannot_reference_a_different_record_hash(tmp_path) -> None:
    import json
    import pytest
    root = tmp_path / "journal" / "2026-27"
    root.mkdir(parents=True)
    payload = {"season": "2026-27", "gameweek": 1, "record_hash": ""}
    from app.journal import record_hash
    payload["record_hash"] = record_hash(payload)
    (root / "gw01.json").write_text(json.dumps(payload), encoding="utf-8")
    (root / "index.json").write_text(json.dumps({
        "gameweeks": [{"gameweek": 1, "record_hash": "wrong"}],
    }), encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError):
        SnapshotRepository(tmp_path).journal_index("2026-27")
