from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.repository import SnapshotRepository


class FakeBlob:
    def __init__(self, text: str, generation: int, updated: datetime):
        self._text = text
        self.generation = generation
        self.updated = updated

    def reload(self) -> None:
        return None

    def download_as_text(self, encoding: str = "utf-8") -> str:
        return self._text


class FakeBucket:
    def __init__(self, blobs: dict[str, FakeBlob]):
        self._blobs = blobs

    def blob(self, name: str) -> FakeBlob:
        return self._blobs[name]


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
