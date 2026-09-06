from __future__ import annotations

import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from app.repository import SnapshotRepository


class FakeBlob:
    def __init__(self, text: str, generation: int, updated: datetime):
        self._text = text
        self.generation = generation
        self.updated = updated
        self.reload_calls = 0
        self.download_calls = 0

    def reload(self) -> None:
        self.reload_calls += 1
        return None

    def download_as_text(self, encoding: str = "utf-8") -> str:
        self.download_calls += 1
        return self._text


class FakeBucket:
    def __init__(self, blobs: dict[str, FakeBlob]):
        self._blobs = blobs

    def blob(self, name: str) -> FakeBlob:
        return self._blobs[name]


class MissingBlob(FakeBlob):
    def reload(self) -> None:
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
