import json
import time
from datetime import datetime, timezone
from threading import Event

import pytest

from app.bounded_io import BoundedIO
from app.repository import SnapshotRepository


def test_hung_sdk_has_deadline_and_no_unbounded_queue():
    io = BoundedIO(2)
    release = Event()
    entered = Event()
    def hung():
        entered.set()
        release.wait(2)
        return "late"
    try:
        for _ in range(2):
            with pytest.raises(TimeoutError):
                io.call(hung, 0.03)
        assert entered.is_set()
        start = time.monotonic()
        with pytest.raises(TimeoutError, match="occupied"):
            io.call(lambda: "third", 1)
        assert time.monotonic() - start < 0.2
    finally:
        release.set()


def test_late_download_cannot_repopulate_negative_cache(tmp_path, monkeypatch):
    release, finished = Event(), Event()
    class Blob:
        generation = 1
        updated = datetime.now(timezone.utc)
        def reload(self, **kwargs):
            pass
        def download_as_text(self, **kwargs):
            release.wait(2)  # SDK deliberately ignores its supplied timeout.
            finished.set()
            return '{"events":[6]}'
    class Bucket:
        def blob(self, _):
            return Blob()
    repo = SnapshotRepository(tmp_path)
    repo._bucket = Bucket()
    repo.REMOTE_TIMEOUT_SECONDS = 0.03
    monkeypatch.setattr("app.repository.remote_io", BoundedIO(1))
    try:
        assert repo._read_remote("bootstrap_cache.json") is None
        release.set()
        assert finished.wait(1)
        assert repo._read_remote("bootstrap_cache.json") is None
        assert not repo._remote_cache
        assert not repo._remote_updated
    finally:
        release.set()


def test_success_cache_and_provenance_are_bounded(tmp_path):
    class Blob:
        generation = 1
        updated = datetime.now(timezone.utc)
        def reload(self, **kwargs):
            pass
        def download_as_text(self, **kwargs):
            return json.dumps({"ok": True})
    class Bucket:
        def blob(self, _):
            return Blob()
    repo = SnapshotRepository(tmp_path)
    repo._bucket = Bucket()
    for index in range(20):
        assert repo._read_remote(f"gw{index}_league1_data.json") == {"ok": True}
    assert len(repo._remote_cache) == repo.SNAPSHOT_CACHE_LIMIT
    assert set(repo._remote_updated) == set(repo._remote_cache)
    assert repo._read_remote("gw0_league1_data.json") == {"ok": True}


def test_coalescing_wait_is_bounded(tmp_path):
    repo = SnapshotRepository(tmp_path)
    repo._bucket = object()
    repo.REMOTE_TIMEOUT_SECONDS = 0.03
    lock = repo._remote_locks[hash("bootstrap_cache.json") % len(repo._remote_locks)]
    lock.acquire()
    try:
        start = time.monotonic()
        assert repo._read_remote("bootstrap_cache.json") is None
        assert time.monotonic() - start < 0.2
    finally:
        lock.release()
