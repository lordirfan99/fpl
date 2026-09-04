from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.live_freshness import snapshot_freshness
from app.repository import LiveSnapshotNotFoundError


NOW = datetime(2026, 9, 4, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize("hours,stale", [(0.5, False), (8, False), (12, False), (12.01, True), (-1, True)])
def test_age_and_overnight_window(hours, stale):
    result = snapshot_freshness((NOW - timedelta(hours=hours)).isoformat(), now=NOW)
    assert result["stale"] is stale


@pytest.mark.parametrize("raw", [None, "bad", "2026-09-04T08:00:00"])
def test_unknown_timestamp_fails_closed(raw):
    assert snapshot_freshness(raw, now=NOW)["stale"] is True


def test_status_has_no_manager_payload_and_reports_staleness(monkeypatch):
    monkeypatch.setattr(main.repository, "live_league", lambda league: {
        "gameweek": 3, "captured_at": "2020-01-01T00:00:00Z", "expected_count": 2,
        "managers": [{"private": "not in health response"}],
    })
    response = TestClient(main.app).get("/v1/leagues/58005/live/status")
    assert response.status_code == 200
    assert response.json()["ready"] is False
    assert "managers" not in response.json()


def test_status_reports_missing_snapshot(monkeypatch):
    def missing(league):
        raise LiveSnapshotNotFoundError("missing")
    monkeypatch.setattr(main.repository, "live_league", missing)
    assert TestClient(main.app).get("/v1/leagues/58005/live/status").status_code == 503
