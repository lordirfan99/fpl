from __future__ import annotations

import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.repository import LiveSnapshotNotFoundError, SnapshotRepository


class FakeBlob:
    def __init__(self, text: str, generation: int, updated: datetime):
        self._text = text
        self.generation = generation
        self.updated = updated
        self.reload_calls = 0
        self.download_calls = 0

    def reload(self, **kwargs) -> None:
        self.reload_calls += 1
        return None

    def download_as_text(self, encoding: str = "utf-8", **kwargs) -> str:
        self.download_calls += 1
        return self._text


class FakeBucket:
    def __init__(self, blobs: dict[str, FakeBlob]):
        self._blobs = blobs

    def blob(self, name: str) -> FakeBlob:
        return self._blobs[name]


class MissingBlob(FakeBlob):
    def reload(self, **kwargs) -> None:
        self.reload_calls += 1
        raise FileNotFoundError("not published")


def _repo(tmp_path: Path, local: dict, remote_blob: FakeBlob | None) -> SnapshotRepository:
    (tmp_path / "bootstrap_cache.json").write_text(json.dumps(local), encoding="utf-8")
    repo = SnapshotRepository(tmp_path)
    if remote_blob is not None:
        repo._bucket = FakeBucket({"snapshots/bootstrap_cache.json": remote_blob})
    return repo


def test_remote_without_fetched_at_still_wins_when_gcs_object_is_newer(tmp_path: Path) -> None:
    """A raw upstream dump (no _meta) published to GCS must beat a stale image copy."""
    local = {"_meta": {"fetched_at": "2026-08-31T12:51:09+00:00"}, "events": [{"id": 1, "finished": True}]}
    remote_payload = {"events": [{"id": 2, "finished": True}]}  # no _meta at all
    blob = FakeBlob(json.dumps(remote_payload), generation=999, updated=datetime(2026, 9, 1, 12, 5, tzinfo=timezone.utc))
    repo = _repo(tmp_path, local, blob)

    resolved = repo._fresh_reference("bootstrap_cache.json")

    assert resolved == remote_payload
    assert repo._remote_updated["bootstrap_cache.json"] > repo._capture_timestamp(local)


def test_stale_remote_without_provenance_does_not_override_newer_image(tmp_path: Path) -> None:
    local = {"_meta": {"fetched_at": "2026-09-01T12:00:00+00:00"}, "events": [{"id": 3}]}
    remote_payload = {"events": [{"id": 1}]}
    blob = FakeBlob(json.dumps(remote_payload), generation=1, updated=datetime(2026, 8, 30, 0, 0, tzinfo=timezone.utc))
    repo = _repo(tmp_path, local, blob)

    assert repo._fresh_reference("bootstrap_cache.json") == local


def test_null_meta_does_not_crash_capture_timestamp() -> None:
    assert SnapshotRepository._capture_timestamp({"_meta": None, "fetched_at": None}) is None
    assert SnapshotRepository._capture_timestamp({"_meta": None, "fetched_at": "2026-09-01T00:00:00Z"}) is not None


def test_remote_snapshot_is_reused_within_revalidation_window(tmp_path: Path) -> None:
    payload = {"_meta": {"fetched_at": "2026-09-01T12:00:00+00:00"}, "events": [{"id": 4}]}
    blob = FakeBlob(json.dumps(payload), generation=2, updated=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))
    repo = _repo(tmp_path, {}, blob)

    assert repo._read_remote("bootstrap_cache.json") == payload
    assert repo._read_remote("bootstrap_cache.json") == payload
    assert blob.reload_calls == 1
    assert blob.download_calls == 1


def test_missing_remote_snapshot_is_negatively_cached(tmp_path: Path) -> None:
    blob = MissingBlob("{}", generation=0, updated=datetime(2026, 9, 1, tzinfo=timezone.utc))
    repo = _repo(tmp_path, {}, blob)

    assert repo._read_remote("bootstrap_cache.json") is None
    assert repo._read_remote("bootstrap_cache.json") is None
    assert blob.reload_calls == 1


def test_failed_refresh_does_not_resurrect_old_payload(tmp_path, monkeypatch):
    blob = FakeBlob('{"events": [4]}', 2, datetime(2026, 9, 1, tzinfo=timezone.utc))
    repo = _repo(tmp_path, {}, blob)
    clock = [100.0]
    monkeypatch.setattr("app.repository.time.monotonic", lambda: clock[0])
    assert repo._read_remote("bootstrap_cache.json") == {"events": [4]}
    clock[0] += 31
    blob.generation = 3
    blob._text = "invalid json"
    assert repo._read_remote("bootstrap_cache.json") is None
    assert repo._read_remote("bootstrap_cache.json") is None
    assert "bootstrap_cache.json" not in repo._remote_updated
    assert blob.reload_calls == 2
    clock[0] += 31
    blob._text = '{"events": [5]}'
    assert repo._read_remote("bootstrap_cache.json") == {"events": [5]}


def test_concurrent_remote_reads_download_once(tmp_path):
    blob = FakeBlob('{"events": [4]}', 2, datetime(2026, 9, 1, tzinfo=timezone.utc))
    repo = _repo(tmp_path, {}, blob)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: repo._read_remote("bootstrap_cache.json"), range(32)))
    assert all(result == {"events": [4]} for result in results)
    assert blob.reload_calls == blob.download_calls == 1


def test_hash_cache_tracks_payload_identity_and_is_bounded(tmp_path, monkeypatch):
    repo = SnapshotRepository(tmp_path)
    payload = {"managers": [1]}
    monkeypatch.setattr(repo, "read", lambda _: payload)
    first = repo.league(1, 1)["_artifact_sha256"]
    payload = {"managers": [2]}
    second = repo.league(1, 1)["_artifact_sha256"]
    assert first != second
    assert second == hashlib.sha256(b'{"managers":[2]}').hexdigest()
    for league_id in range(20):
        repo.league(league_id, 1)
    assert len(repo._hash_cache) == 16


def live_repo(tmp_path):
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    payload = {"status": "complete", "league_id": 1, "expected_count": 1,
               "hydrated_count": 1, "managers": [{"id": 1}], "fetched_at": now.isoformat()}
    raw = json.dumps(payload)
    name = "live/gw3/league1/runs/one.json"
    manifest = {"status": "complete", "snapshot_object": name,
                "snapshot_sha256": hashlib.sha256(raw.encode()).hexdigest()}
    pointer = FakeBlob(json.dumps(manifest), 1, now)
    data = FakeBlob(raw, 1, now)
    repo = SnapshotRepository(tmp_path)
    repo._bucket = FakeBucket({"live/league1/current.json": pointer, name: data})
    return repo, pointer, data, manifest


def test_live_reads_coalesce_and_revalidate_pointer_without_redownload(tmp_path, monkeypatch):
    repo, pointer, data, _ = live_repo(tmp_path)
    clock = [100.0]
    monkeypatch.setattr("app.repository.time.monotonic", lambda: clock[0])
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: repo.live_league(1), range(32)))
    assert all(row is results[0] for row in results)
    assert pointer.download_calls == data.download_calls == 1
    clock[0] += 31
    assert repo.live_league(1) is results[0]
    assert pointer.download_calls == 2
    assert data.download_calls == 1
    assert repo.live_league(1)["fetched_at"] == "2026-09-06T00:00:00+00:00"


def test_live_changed_pointer_and_failed_revalidation(tmp_path, monkeypatch):
    repo, pointer, data, manifest = live_repo(tmp_path)
    clock = [100.0]
    monkeypatch.setattr("app.repository.time.monotonic", lambda: clock[0])
    assert repo.live_league(1)["managers"][0]["id"] == 1
    clock[0] += 31
    changed = json.loads(data._text)
    changed["managers"][0]["id"] = 2
    data._text = json.dumps(changed)
    manifest["snapshot_sha256"] = hashlib.sha256(data._text.encode()).hexdigest()
    pointer._text = json.dumps(manifest)
    assert repo.live_league(1)["managers"][0]["id"] == 2
    clock[0] += 31
    pointer._text = "bad json"
    for _ in range(2):
        with pytest.raises(LiveSnapshotNotFoundError):
            repo.live_league(1)
    assert pointer.download_calls == 3
    clock[0] += 31
    pointer._text = json.dumps(manifest)
    assert repo.live_league(1)["managers"][0]["id"] == 2


def test_live_checksum_failure_never_enters_success_cache(tmp_path):
    repo, pointer, data, _ = live_repo(tmp_path)
    data._text += " "
    for _ in range(2):
        with pytest.raises(LiveSnapshotNotFoundError):
            repo.live_league(1)
    assert pointer.download_calls == data.download_calls == 1


def test_live_cache_bounds_failed_and_successful_entries(tmp_path):
    repo, _, _, _ = live_repo(tmp_path)
    assert repo.live_league(1)["expected_count"] == 1
    for league in range(2, 10):
        with pytest.raises(LiveSnapshotNotFoundError):
            repo.live_league(league)
        assert len(repo._live_cache) <= repo.LIVE_CACHE_LIMIT
    assert 1 not in repo._live_cache
    assert repo.live_league(1)["expected_count"] == 1


def test_live_wrong_league_pointer_never_serves_or_downloads_another_league(tmp_path):
    repo, pointer, data, manifest = live_repo(tmp_path)
    manifest["snapshot_object"] = "live/gw3/league2/runs/one.json"
    pointer._text = json.dumps(manifest)
    with pytest.raises(LiveSnapshotNotFoundError):
        repo.live_league(1)
    assert data.download_calls == 0


def compact_repo(tmp_path):
    repo, pointer, full, manifest = live_repo(tmp_path)
    manifest.update(league_id=1, gameweek=3, expected_count=1, hydrated_count=1, captured_at="2026-09-06T00:00:00+00:00")
    compact = {"schema_version": 1, "league_id": 1, "owner_entry_id": 10, "gameweek": 3,
               "snapshot_at": manifest["captured_at"], "snapshot_sha256": manifest["snapshot_sha256"],
               "goal": {"available": True}, "ownership": {"rows": []}}
    raw = json.dumps(compact)
    name = manifest["snapshot_object"].removesuffix(".json") + "-decision.json"
    manifest.update(decision_object=name, decision_sha256=hashlib.sha256(raw.encode()).hexdigest())
    pointer._text = json.dumps(manifest)
    summary = FakeBlob(raw, 1, datetime(2026, 9, 6, tzinfo=timezone.utc))
    repo._bucket._blobs[name] = summary
    return repo, pointer, full, summary, manifest


def test_compact_context_never_downloads_full_squads_and_revalidates(tmp_path, monkeypatch):
    repo, pointer, full, summary, _ = compact_repo(tmp_path)
    clock = [100.0]
    monkeypatch.setattr("app.repository.time.monotonic", lambda: clock[0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: repo.live_decision_context(1, 10), range(16)))
    assert all(result["owner_entry_id"] == 10 for result in results)
    assert pointer.download_calls == summary.download_calls == 1
    assert full.download_calls == 0
    clock[0] += 31
    assert repo.live_decision_context(1, 10)["gameweek"] == 3
    assert pointer.download_calls == 2
    assert summary.download_calls == 1


@pytest.mark.parametrize("field,value", [("owner_entry_id", 99), ("league_id", 2), ("gameweek", 4),
                                        ("snapshot_at", "wrong"), ("snapshot_sha256", "wrong")])
def test_compact_identity_mismatch_fails_closed(tmp_path, field, value):
    repo, pointer, full, summary, manifest = compact_repo(tmp_path)
    payload = json.loads(summary._text)
    payload[field] = value
    summary._text = json.dumps(payload)
    manifest["decision_sha256"] = hashlib.sha256(summary._text.encode()).hexdigest()
    pointer._text = json.dumps(manifest)
    with pytest.raises(LiveSnapshotNotFoundError):
        repo.live_decision_context(1, 10)
    assert full.download_calls == 0


def test_compact_checksum_failure_discards_previous_cache(tmp_path, monkeypatch):
    repo, pointer, full, summary, manifest = compact_repo(tmp_path)
    clock = [100.0]
    monkeypatch.setattr("app.repository.time.monotonic", lambda: clock[0])
    assert repo.live_decision_context(1, 10)
    clock[0] += 31
    manifest["decision_sha256"] = "0" * 64
    pointer._text = json.dumps(manifest)
    with pytest.raises(LiveSnapshotNotFoundError):
        repo.live_decision_context(1, 10)
    assert (1, 10) not in repo._decision_cache
    assert full.download_calls == 0


def test_legacy_manifest_can_use_existing_full_read(tmp_path):
    repo, pointer, _, _, manifest = compact_repo(tmp_path)
    manifest.pop("decision_object")
    manifest.pop("decision_sha256")
    pointer._text = json.dumps(manifest)
    assert repo.live_decision_context(1, 10) is None


def test_liveness_never_reads_cloud_storage(monkeypatch):
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app.main import app
    with patch("app.main._readiness", side_effect=AssertionError("Unexpected storage read")):
        result = TestClient(app).get("/livez")
    assert result.status_code == 200
    assert result.json()["writes_enabled"] is False
