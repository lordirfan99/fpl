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
                                "meta": {"stale": False}}).encode(), {"server-timing": "ok"}

    monkeypatch.setattr(monitor, "fetch", fetch)
    assert monitor.main() == 0
    assert any(url.endswith("/v1/leagues/58005/live/status") for url in calls)
    assert any(url.endswith("/v1/leagues/131997/live/status") for url in calls)
    assert any("/v1/decision/current?league_id=131997" in url for url in calls)


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
