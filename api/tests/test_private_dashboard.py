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
    with patch("app.private_dashboard.read_private") as read:
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
    with patch("app.private_dashboard.read_private", side_effect=[packet, check]):
        response = TestClient(app).get("/v1/private/dashboard/current", headers={"Authorization": "Bearer " + "a" * 40})
        assert response.json()["packet"] is None
        assert "must never appear" not in response.text
