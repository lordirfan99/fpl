"""Read-scoped private endpoint. No public repository fallback, no FPL client."""
import hmac
import json
import os
import time
from datetime import datetime, timezone
from functools import lru_cache
from threading import Lock

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
HEADERS = {"Cache-Control": "private, no-store", "Vary": "Authorization"}
READ_TIMEOUT_SECONDS = 2
PAIR_CACHE_SECONDS = 5
_pair_lock = Lock()
_pair_cache = {}


def recent(value, now, seconds):
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return instant.tzinfo is not None and 0 <= (now - instant).total_seconds() <= seconds
    except (TypeError, ValueError, AttributeError):
        return False


def validate(packet, check, owner, now=None):
    now = now or datetime.now(timezone.utc)
    reasons = []
    if packet.get("schema_version") != 1 or check.get("schema_version") != 1:
        reasons.append("unsupported_schema")
    if packet.get("team_id") != owner or check.get("team_id") != owner:
        reasons.append("owner_mismatch")
    if check.get("verified") is not True or not recent(check.get("checked_at"), now, 20 * 60):
        reasons.append("account_check_unavailable")
    fingerprint = packet.get("account_fingerprint")
    if not fingerprint or fingerprint != check.get("account_fingerprint"):
        reasons.append("account_changed")
    if packet.get("plan_id") != check.get("plan_id"):
        reasons.append("plan_superseded")
    if not packet.get("plan_id") or not recent(packet.get("generated_at"), now, 12 * 3600):
        reasons.append("plan_expired")
    for source in ("reference", "league", "account"):
        if not recent((packet.get("timestamps") or {}).get(source), now, 12 * 3600):
            reasons.append(f"{source}_stale")
    try:
        deadline = datetime.fromisoformat(packet["deadline"].replace("Z", "+00:00"))
        if deadline.tzinfo is None or deadline <= now:
            reasons.append("deadline_passed")
    except (KeyError, ValueError, TypeError, AttributeError):
        reasons.append("deadline_unknown")
    return reasons


@lru_cache(maxsize=2)
def private_bucket(bucket_name):
    from google.cloud import storage
    return storage.Client().bucket(bucket_name)


def read_private(name, bucket):
    return json.loads(bucket.blob(f"dashboard/{name}.json").download_as_text(
        encoding="utf-8", timeout=READ_TIMEOUT_SECONDS, retry=None))


def read_private_pair():
    bucket_name, public_bucket = os.getenv("FPL_PRIVATE_DASHBOARD_BUCKET"), os.getenv("FPL_SNAPSHOT_BUCKET")
    if not bucket_name or not public_bucket or bucket_name == public_bucket:
        raise ValueError("Private bucket unavailable")
    # One client, one protection check, and one coalesced read pair. Keep this
    # short-lived and validate identity, account age and deadline on EVERY hit.
    if not _pair_lock.acquire(timeout=1):
        raise TimeoutError("Private dashboard read is busy")
    try:
        key = (bucket_name, public_bucket)
        cached = _pair_cache.get(key)
        if cached and time.monotonic() - cached[0] < PAIR_CACHE_SECONDS:
            if cached[1] is None:
                raise ValueError("Private data unavailable")
            return cached[1]
        try:
            bucket = private_bucket(bucket_name)
            bucket.reload(timeout=READ_TIMEOUT_SECONDS, retry=None)
            if bucket.iam_configuration.public_access_prevention != "enforced":
                raise ValueError("Private bucket protection missing")
            pair = read_private("plan", bucket), read_private("account-check", bucket)
        except Exception:
            _pair_cache.clear()
            _pair_cache[key] = (time.monotonic(), None)
            raise
        _pair_cache.clear()
        _pair_cache[key] = (time.monotonic(), pair)
        return pair
    finally:
        _pair_lock.release()


@router.get("/v1/private/dashboard/current")
def current(request: Request):
    secret = os.getenv("FPL_DASHBOARD_READ_TOKEN", "")
    supplied = request.headers.get("authorization", "")
    if len(secret) < 32 or not hmac.compare_digest(supplied.encode(), f"Bearer {secret}".encode()):
        return JSONResponse({"error": "Unauthorized"}, status_code=401, headers=HEADERS)
    try:
        packet, check = read_private_pair()
        reasons = validate(packet, check, int(os.getenv("FPL_MY_TEAM_ID", "2797967")))
        if not reasons:
            return JSONResponse({"status": "ready", "packet": packet, "account_checked_at": check["checked_at"]}, headers=HEADERS)
    except Exception:
        reasons = ["private_data_unavailable"]
    return JSONResponse({"status": "unavailable", "reasons": reasons, "packet": None}, headers=HEADERS)
