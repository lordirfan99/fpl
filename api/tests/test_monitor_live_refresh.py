import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "monitor_live_test", Path(__file__).resolve().parents[2] / "infra/scripts/monitor_production.py"
)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


@pytest.mark.parametrize("ready", [False, None])
def test_monitor_rejects_stale_or_unknown_live_snapshot(monkeypatch, ready):
    calls = []

    def fetch(url, limit):
        calls.append(url)
        payload = {"ready": True} if url.endswith("/ready") else {"ready": ready}
        return 200, json.dumps(payload).encode(), {}

    monkeypatch.setattr(monitor, "fetch", fetch)
    with pytest.raises(RuntimeError, match="Live league 58005"):
        monitor.main()
    assert calls[-1].endswith("/v1/leagues/58005/live/status")


def test_monitor_checks_both_leagues_before_reporting_success(monkeypatch):
    calls = []

    def fetch(url, limit):
        calls.append(url)
        return 200, json.dumps({"ready": True, "managers": [], "packet_status": "advisory",
                                "freshness": {"status": "provisional", "stale": False},
                                "meta": {"stale": False}}).encode(), {"Server-Timing": "ok"}

    monkeypatch.setattr(monitor, "fetch", fetch)
    assert monitor.main() == 0
    assert any(url.endswith("/v1/leagues/58005/live/status") for url in calls)
    assert any(url.endswith("/v1/leagues/131997/live/status") for url in calls)
    assert any("/v1/decision/current?league_id=131997" in url for url in calls)


def test_lightweight_monitor_checks_login_without_rendering_heavy_pages(monkeypatch):
    calls = []

    def fetch(url, limit):
        calls.append(url)
        return 200, json.dumps({"ready": True, "managers": [], "packet_status": "advisory",
                                "freshness": {"status": "provisional", "stale": False},
                                "meta": {"stale": False}}).encode(), {"server-timing": "ok"}

    monkeypatch.setattr(monitor, "fetch", fetch)
    assert monitor.main(lightweight=True) == 0
    assert any(url.endswith("/sign-in") for url in calls)
    assert not any(url.endswith(("/league", "/compare", "/journal")) for url in calls)


@pytest.mark.parametrize("packet", ["safe_hold", "needs_refresh"])
def test_monitor_accepts_honest_hold_but_not_actions(packet):
    monitor.validate_recommendation({"packet_status": packet, "transfers": []})
    with pytest.raises(RuntimeError):
        monitor.validate_recommendation({"packet_status": packet, "transfers": [{"incoming": 1}]})


def test_monitor_rejects_missing_contract_and_unverified_personal_action():
    with pytest.raises(RuntimeError):
        monitor.validate_recommendation({})
    with pytest.raises(RuntimeError, match="Unverified"):
        monitor.validate_recommendation({"packet_status": "advisory", "meta": {"stale": False},
                                         "freshness": {"status": "provisional", "stale": False},
                                         "captains": [{"element": 1}]})


def _fresh_rec(**freshness):
    base = {"status": "fresh", "stale": False, "rank_provenance": "official-entry-history",
            "bank_known": True, "account_state_verified": True}
    base.update(freshness)
    return {"packet_status": "advisory", "meta": {"stale": False}, "freshness": base}


def test_monitor_accepts_fresh_packet_with_official_provenance():
    monitor.validate_recommendation(_fresh_rec())


@pytest.mark.parametrize("provenance", ["classic-league-rank-fallback", "unknown", "none"])
def test_monitor_flags_degraded_rank_provenance_on_fresh_packet(provenance):
    with pytest.raises(RuntimeError, match="rank_provenance"):
        monitor.validate_recommendation(_fresh_rec(rank_provenance=provenance))


def test_monitor_flags_fresh_packet_with_unknown_bank():
    with pytest.raises(RuntimeError, match="bank_known"):
        monitor.validate_recommendation(_fresh_rec(bank_known=False))


def test_monitor_ignores_provenance_on_finalized_fallback():
    # status != "fresh" -> finalized snapshot path; classic-league rank there is
    # not the same failure and must not trip the fresh-only guard.
    monitor.validate_recommendation({"packet_status": "advisory", "meta": {"stale": False},
                                     "freshness": {"status": "provisional", "stale": False,
                                                   "rank_provenance": "finalized-snapshot",
                                                   "account_state_verified": True}})


NOW = dt.datetime(2026, 9, 4, 12, tzinfo=dt.timezone.utc)
EVENTS = [
    {"id": 2, "deadline_time": "2026-08-28T17:30:00Z", "finished": True},
    {"id": 3, "deadline_time": "2026-09-04T17:30:00Z", "finished": False, "is_next": True},
    {"id": 4, "deadline_time": "2026-09-11T17:30:00Z", "finished": False},
]


def test_next_deadline_picks_first_upcoming_unfinished_event():
    assert monitor.next_deadline(EVENTS, now=NOW) == (3, dt.datetime(2026, 9, 4, 17, 30, tzinfo=dt.timezone.utc))
    assert monitor.next_deadline([], now=NOW) is None


def test_plan_currency_fails_when_advisory_lags_the_imminent_deadline():
    with pytest.raises(RuntimeError, match="GW2 with 5.5h to the GW3 deadline"):
        monitor.check_plan_currency({"packet_status": "advisory", "gameweek": 2}, EVENTS, now=NOW)


def test_plan_currency_passes_when_packet_matches_or_deadline_is_far():
    assert "GW3" in monitor.check_plan_currency({"packet_status": "advisory", "gameweek": 3}, EVENTS, now=NOW)
    far = dt.datetime(2026, 9, 1, 12, tzinfo=dt.timezone.utc)  # >24h before the GW3 lock
    assert "GW2" in monitor.check_plan_currency({"packet_status": "advisory", "gameweek": 2}, EVENTS, now=far)


def test_plan_currency_not_applicable_to_an_honest_hold():
    assert "hold" in monitor.check_plan_currency({"packet_status": "safe_hold", "gameweek": 2}, EVENTS, now=NOW)
