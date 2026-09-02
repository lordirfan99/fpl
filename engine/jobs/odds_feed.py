"""Integrity and freshness metadata for the live football-data odds CSV."""
import datetime
import hashlib
import json
import os
import tempfile


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metadata_path(csv_path):
    return csv_path + ".meta.json"


def _utc(value=None):
    value = value or datetime.datetime.now(datetime.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def write_metadata(csv_path, source_url, fetched_at=None):
    fetched = _utc(fetched_at)
    meta = {
        "schema_version": 1,
        "source_url": source_url,
        "fetched_at": fetched.isoformat().replace("+00:00", "Z"),
        "size": os.path.getsize(csv_path),
        "sha256": sha256_file(csv_path),
    }
    target = metadata_path(csv_path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
            f.write("\n")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return meta


def load_fresh_metadata(csv_path, now=None, max_age_hours=12):
    if not os.path.exists(csv_path):
        return None, "odds CSV missing"
    target = metadata_path(csv_path)
    if not os.path.exists(target):
        return None, "odds metadata missing"
    try:
        with open(target, encoding="utf-8") as f:
            meta = json.load(f)
        fetched = datetime.datetime.fromisoformat(meta["fetched_at"].replace("Z", "+00:00"))
        fetched = _utc(fetched)
        current = _utc(now)
        age_hours = (current - fetched).total_seconds() / 3600
        if age_hours < -0.1:
            return None, "odds metadata timestamp is in the future"
        if age_hours > float(max_age_hours):
            return None, f"odds feed stale ({age_hours:.1f}h old; max {max_age_hours}h)"
        if int(meta.get("size", -1)) != os.path.getsize(csv_path):
            return None, "odds file metadata mismatch"
        if meta.get("sha256") != sha256_file(csv_path):
            return None, "odds file hash mismatch"
        return meta, None
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        return None, f"odds metadata invalid ({type(exc).__name__})"


def fresh_signature(csv_path, now=None, max_age_hours=12):
    meta, _ = load_fresh_metadata(csv_path, now=now, max_age_hours=max_age_hours)
    return meta["sha256"] if meta else None
