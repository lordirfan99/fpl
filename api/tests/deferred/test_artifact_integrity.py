from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

from app.validation import snapshot_quality
from app.repository import SnapshotRepository


ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def content_hash(payload: dict, field: str) -> str:
    body = dict(payload)
    body.pop(field, None)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def test_versioned_full_and_compact_snapshots_are_structurally_aligned() -> None:
    for league in (58005, 131997):
        full = read(DATA / f"gw1_league{league}_data.json")
        compact = read(DATA / f"gw1_league{league}_compact.json")
        status, issues = snapshot_quality(full)
        assert status == "valid", issues[:5]
        assert full["gw"] == compact["gw"] == 1
        assert full["league_id"] == compact["league_id"] == league
        assert full["total_entries"] == len(full["competitors"])
        assert compact["total_entries"] == len(compact["competitors"])
        assert {row["entry_id"] for row in full["competitors"]} == {
            row["entry_id"] for row in compact["competitors"]
        }


def test_frozen_journal_hashes_and_index_references_are_valid() -> None:
    index = read(DATA / "journal" / "2026-27" / "index.json")
    for row in index["gameweeks"]:
        entry = read(DATA / "journal" / "2026-27" / f"gw{int(row['gameweek']):02d}.json")
        assert entry["record_hash"] == content_hash(entry, "record_hash")
        assert row["record_hash"] == entry["record_hash"]


def test_predeadline_bundle_hash_target_and_cutoff_are_valid() -> None:
    path = DATA / "journal-raw" / "2026-27" / "gw02" / "predeadline.json"
    if not path.is_file():
        pytest.skip("private pre-deadline evidence is verified in its immutable cloud store")
    bundle = read(path)
    assert bundle["input_hash"] == content_hash(bundle, "input_hash")
    captured = datetime.fromisoformat(bundle["captured_at"].replace("Z", "+00:00"))
    deadline = datetime.fromisoformat(bundle["deadline"].replace("Z", "+00:00"))
    assert captured < deadline
    assert bundle["decision"]["gameweek"] == bundle["gameweek"]
    assert bundle["v5"]["gameweek"] == bundle["gameweek"]
    assert bundle["v5"]["projection_version"] == "projection-v5.0-lab"


def test_fixture_cache_covers_the_full_season_without_duplicate_fixture_ids() -> None:
    cache = read(DATA / "fixtures_cache.json")
    assert set(map(int, cache["gameweeks"])) == set(range(1, 39))
    fixture_keys = [
        (gameweek, fixture.get("team_h"), fixture.get("team_a"), fixture.get("kickoff_time"))
        for gameweek, rows in cache["gameweeks"].items()
        for fixture in rows
    ]
    assert all(home and away for _, home, away, _ in fixture_keys)
    assert len(fixture_keys) == len(set(fixture_keys))


def test_bootstrap_provenance_hash_matches_the_official_payload_body() -> None:
    bootstrap = read(DATA / "bootstrap_cache.json")
    meta = bootstrap.pop("_meta")
    calculated = hashlib.sha256(
        json.dumps(bootstrap, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert meta["source"] == "official-fpl-api/bootstrap-static"
    assert meta["fetched_at"]
    assert meta["content_sha256"] == calculated


def test_reference_cache_prefers_newest_proven_source(tmp_path: Path, monkeypatch) -> None:
    local = {
        "elements": [{"id": 1}],
        "_meta": {"fetched_at": "2026-08-31T12:00:00+00:00"},
    }
    (tmp_path / "bootstrap_cache.json").write_text(json.dumps(local), encoding="utf-8")
    repository = SnapshotRepository(tmp_path)
    monkeypatch.setattr(
        repository,
        "_read_remote",
        lambda _filename: {"elements": [{"id": 2}]},
    )
    assert repository.bootstrap()["elements"] == [{"id": 1}]

    monkeypatch.setattr(
        repository,
        "_read_remote",
        lambda _filename: {
            "elements": [{"id": 3}],
            "_meta": {"fetched_at": "2026-08-31T13:00:00+00:00"},
        },
    )
    assert repository.bootstrap()["elements"] == [{"id": 3}]
