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
    collector._validate([{"entry_id": 1, "squad": squad, "active_chip": "bboost"}], 1)


@pytest.mark.parametrize("count", [0, 10, 12, 14])
def test_incomplete_scoring_lineup_is_rejected(count):
    squad = _squad()
    for index, pick in enumerate(squad):
        pick["multiplier"] = int(index < count)
    with pytest.raises(RuntimeError, match="invalid lineup"):
        collector._validate([{"entry_id": 1, "squad": squad}], 1)


@pytest.mark.parametrize("fail_summary", [False, True])
def test_publish_binds_compact_evidence_to_snapshot_before_manifest(monkeypatch, tmp_path, fail_summary):
    import hashlib
    import json
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.repository import SnapshotRepository

    objects, writes = {}, []
    class Blob:
        generation = 1
        def __init__(self, name):
            self.name = name
        def upload_from_string(self, value, **kwargs):
            assert kwargs["if_generation_match"] == 0
            if fail_summary and self.name.endswith("-decision.json"):
                raise RuntimeError("summary upload failed")
            objects[self.name] = value
            writes.append(self.name)
        def exists(self):
            return self.name in objects
        def download_as_text(self, **kwargs):
            return objects[self.name].decode()
    class Bucket:
        def blob(self, name):
            return Blob(name)
    bucket = Bucket()
    monkeypatch.setattr(collector, "storage", SimpleNamespace(Client=lambda: SimpleNamespace(bucket=lambda _: bucket)))
    monkeypatch.setenv("FPL_MY_TEAM_ID", "1")
    payload = {"status": "complete", "league_id": 58005, "gameweek": 5,
               "captured_at": datetime.now(timezone.utc).isoformat(), "expected_count": 1, "hydrated_count": 1,
               "managers": [{"entry_id": 1, "league_rank": 1, "total_points": 100, "squad": _squad()}]}
    if fail_summary:
        with pytest.raises(RuntimeError, match="summary upload"):
            collector.publish("test", payload)
        assert "live/league58005/current.json" not in objects
        return
    name, digest = collector.publish("test", payload)
    manifest = json.loads(objects["live/league58005/current.json"])
    assert writes == [name, manifest["decision_object"], "live/league58005/current.json"]
    assert hashlib.sha256(objects[name]).hexdigest() == digest == manifest["snapshot_sha256"]
    repo = SnapshotRepository(tmp_path)
    repo._bucket = bucket
    evidence = repo.live_decision_context(58005, 1)
    assert evidence["goal"]["points_gap"] == 0
    assert evidence["ownership"]["sample_count"] == 1
    assert evidence["snapshot_sha256"] == digest
    assert "managers" not in evidence



def test_reference_publication_supplies_api_paths_and_provenance(monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace
    bootstrap = {"elements": [{"id": i, "web_name": f"Player {i}"} for i in range(1, 401)],
                 "teams": [{"id": i, "name": f"Club {i}"} for i in range(1, 21)],
                 "events": [{"id": i, "is_current": i == 6} for i in range(1, 39)],
                 "element_types": [{"id": i} for i in range(1, 5)]}
    fixtures = [{"id": i, "event": 7, "team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 3}
                for i in range(1, 381)]
    uploads = {}
    class Blob:
        def __init__(self, name):
            self.name = name
        def upload_from_string(self, raw, **kwargs):
            assert kwargs["timeout"] == 15 and kwargs["retry"] is None
            uploads[self.name] = json.loads(raw)
    bucket = SimpleNamespace(blob=lambda name: Blob(name))
    monkeypatch.setattr(collector, "storage", SimpleNamespace(Client=lambda: SimpleNamespace(bucket=lambda _: bucket)))
    monkeypatch.setattr(collector.live_fpl, "_get", lambda path, **_: bootstrap if path == "bootstrap-static/" else fixtures)
    collector.publish_references("test-bucket")
    assert set(uploads) == {"snapshots/bootstrap_cache.json", "snapshots/fixtures_cache.json"}
    published = uploads["snapshots/bootstrap_cache.json"]
    assert published["_meta"]["content_sha256"] == hashlib.sha256(collector._canonical(bootstrap)).hexdigest()
    assert published["_meta"]["fetched_at"] == uploads["snapshots/fixtures_cache.json"]["fetched_at"]
    assert published["events"] == bootstrap["events"]
    assert uploads["snapshots/fixtures_cache.json"]["gameweeks"]["7"][0]["team_h"] == "Club 1"
    assert "_meta" not in bootstrap
    horizon = uploads["snapshots/fixtures_cache.json"]
    assert horizon["schema_version"] == 2 and horizon["fixture_count"] == 380
    assert horizon["source_url"].endswith("/api/fixtures/")
    assert horizon["content_sha256"] == hashlib.sha256(json.dumps(fixtures, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert len(horizon["gameweeks"]) == 38
    assert published["_meta"]["source"] == "official-fpl-api/bootstrap-static"


def test_reference_publication_seeds_atomic_local_api_paths(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    bootstrap = {"elements": [{"id": i, "web_name": f"Player {i}"} for i in range(1, 401)],
                 "teams": [{"id": i, "name": f"Club {i}"} for i in range(1, 21)],
                 "events": [{"id": i} for i in range(1, 39)],
                 "element_types": [{"id": i} for i in range(1, 5)]}
    fixtures = [{"id": i, "event": 7, "team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 3}
                for i in range(1, 381)]
    class Blob:
        def upload_from_string(self, raw, **kwargs):
            assert json.loads(raw)
    bucket = SimpleNamespace(blob=lambda _: Blob())
    monkeypatch.setattr(collector, "storage", SimpleNamespace(Client=lambda: SimpleNamespace(bucket=lambda _: bucket)))
    monkeypatch.setattr(collector.live_fpl, "_get", lambda path, **_: bootstrap if path == "bootstrap-static/" else fixtures)

    collector.publish_references("test-bucket", tmp_path)

    for name in ("bootstrap_cache.json", "fixtures_cache.json"):
        assert json.loads((tmp_path / name).read_text(encoding="utf-8"))
        assert not list(tmp_path.glob(f".{name}.*.tmp"))


def test_references_only_never_collects_leagues(monkeypatch):
    calls = []
    monkeypatch.setattr("sys.argv", ["refresh", "--bucket", "test", "--references-only"])
    monkeypatch.setattr(collector, "publish_references", lambda bucket, local_data_dir=None: calls.append((bucket, local_data_dir)))
    monkeypatch.setattr(collector, "collect", lambda _: pytest.fail("League collection was requested"))
    assert collector.main() == 0
    assert calls == [("test", None)]


def test_invalid_reference_does_not_publish(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(collector, "storage", SimpleNamespace(Client=lambda: pytest.fail("Invalid references must not publish")))
    monkeypatch.setattr(collector.live_fpl, "_get", lambda path, **_: {} if path == "bootstrap-static/" else [])
    with pytest.raises(RuntimeError):
        collector.publish_references("test")
