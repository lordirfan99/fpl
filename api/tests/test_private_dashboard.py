from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.private_dashboard import validate

NOW = datetime(2026, 9, 4, 15, tzinfo=timezone.utc)


def pair():
    packet = {"schema_version": 1, "team_id": 2797967, "plan_id": "canonical", "account_fingerprint": "abc",
              "generated_at": NOW.isoformat(), "deadline": (NOW + timedelta(hours=2)).isoformat(),
              "timestamps": dict.fromkeys(["account", "reference", "league"], NOW.isoformat())}
    check = {"schema_version": 1, "team_id": 2797967, "plan_id": "canonical", "verified": True,
             "checked_at": NOW.isoformat(), "account_fingerprint": "abc"}
    return packet, check


def test_valid_and_no_mutation():
    packet, check = pair()
    original = deepcopy(packet)
    assert validate(packet, check, 2797967, NOW) == []
    assert packet == original


@pytest.mark.parametrize("change,reason", [
    ({"verified": False}, "account_check_unavailable"),
    ({"account_fingerprint": "new"}, "account_changed"),
    ({"plan_id": "new"}, "plan_superseded"),
    ({"team_id": 1}, "owner_mismatch"),
    ({"checked_at": (NOW - timedelta(minutes=21)).isoformat()}, "account_check_unavailable"),
    ({"checked_at": (NOW + timedelta(seconds=1)).isoformat()}, "account_check_unavailable"),
    ({"checked_at": "2026-09-04T15:00:00"}, "account_check_unavailable"),
])
def test_account_invalidation(change, reason):
    packet, check = pair()
    check.update(change)
    assert reason in validate(packet, check, 2797967, NOW)


def test_deadline_rollover_and_stale_sources():
    packet, check = pair()
    assert "deadline_passed" in validate(packet, check, 2797967, NOW + timedelta(hours=2))
    packet["timestamps"]["league"] = None
    assert "league_stale" in validate(packet, check, 2797967, NOW)


def test_unauthorized_never_reads_storage(monkeypatch):
    monkeypatch.setenv("FPL_DASHBOARD_READ_TOKEN", "a" * 40)
    with patch("app.private_dashboard.read_private_pair") as read:
        response = TestClient(app).get("/v1/private/dashboard/current")
        assert response.status_code == 401
        assert response.headers["cache-control"] == "private, no-store"
        read.assert_not_called()
        assert TestClient(app).post("/v1/private/dashboard/current").status_code == 405


def test_authorized_stale_packet_is_not_returned(monkeypatch):
    monkeypatch.setenv("FPL_DASHBOARD_READ_TOKEN", "a" * 40)
    packet, check = pair()
    packet["secret_sentinel"] = "must never appear"
    check["verified"] = False
    with patch("app.private_dashboard.read_private_pair", return_value=(packet, check)):
        response = TestClient(app).get("/v1/private/dashboard/current", headers={"Authorization": "Bearer " + "a" * 40})
        assert response.json()["packet"] is None
        assert "must never appear" not in response.text


def test_private_pair_coalesces_and_rechecks_protection_after_cache_expires(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app import private_dashboard as private
    monkeypatch.setenv("FPL_PRIVATE_DASHBOARD_BUCKET", "private-test")
    monkeypatch.setenv("FPL_SNAPSHOT_BUCKET", "public-test")
    private._pair_cache.clear()
    clock = [100.0]
    monkeypatch.setattr(private.time, "monotonic", lambda: clock[0])
    bucket = Mock()
    bucket.iam_configuration = SimpleNamespace(public_access_prevention="enforced")
    monkeypatch.setattr(private, "private_bucket", lambda _: bucket)
    packet, check = pair()
    with patch("app.private_dashboard.read_private", side_effect=[packet, check]) as read:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: private.read_private_pair(), range(16)))
        assert all(result == (packet, check) for result in results)
        assert read.call_count == 2
        bucket.reload.assert_called_once_with(timeout=2, retry=None)
    clock[0] += 6
    bucket.iam_configuration.public_access_prevention = "inherited"
    with patch("app.private_dashboard.read_private") as read:
        with pytest.raises(ValueError, match="protection"):
            private.read_private_pair()
        read.assert_not_called()
        # No cached successful plan can survive a failed protection refresh.
        with pytest.raises(ValueError, match="unavailable"):
            private.read_private_pair()
    private._pair_cache.clear()


def test_cached_pair_still_invalidates_at_account_expiry(monkeypatch):
    monkeypatch.setenv("FPL_DASHBOARD_READ_TOKEN", "a" * 40)
    now = datetime.now(timezone.utc)
    packet, check = pair()
    packet.update(generated_at=now.isoformat(), deadline=(now + timedelta(hours=1)).isoformat(),
                  timestamps=dict.fromkeys(["account", "reference", "league"], now.isoformat()))
    check["checked_at"] = (now - timedelta(minutes=21)).isoformat()
    with patch("app.private_dashboard.read_private_pair", return_value=(packet, check)):
        response = TestClient(app).get("/v1/private/dashboard/current", headers={"Authorization": "Bearer " + "a" * 40})
    assert response.json()["packet"] is None
    assert "account_check_unavailable" in response.json()["reasons"]
