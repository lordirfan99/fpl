import importlib.util
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "monitor_live_test", Path(__file__).resolve().parents[2] / "infra/scripts/monitor_production.py"
)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)

def fake_success(url):
    pages = {
        "/": "Your next FPL decision",
        "/v2": "Your next FPL decision",
        "/v2/league?league=131997": "Complete standings",
        "/v2/elite?league=131997": "The top five percent",
        "/v2/team": "My FPL team",
        "/v2/players": "Official recorded player pool",
        "/v2/compare?league=131997": "Compare recorded managers",
        "/v2/planner": "Five gameweeks ahead",
        "/v2/journal": "Season decision journal",
        "/sign-in": "Your private decision room",
        "/this-week": "This gameweek",
        "/settings": "Your dashboard preferences",
    }
    if url.startswith(monitor.SITE):
        path = url.removeprefix(monitor.SITE)
        return 200, pages.get(path, "").encode(), {"Server-Timing": "ok"}
    if url.startswith(monitor.PUBLIC_DATA):
        catalog = b'{"players":[]}'
        if url.endswith("/manifest.json"):
            required = ("catalog.json", "fixtures.json", "leagues/58005/live.json",
                        "leagues/131997/live.json", "journal/2026-27/index.json")
            files = {name: "a" * 64 for name in required}
            files["catalog.json"] = hashlib.sha256(catalog).hexdigest()
            return 200, json.dumps({
                "schema_version": 1, "release": "f" * 64, "files": files,
                "writes_enabled": False, "generated_at": datetime.now(timezone.utc).isoformat()
            }).encode(), {}
        if url.endswith("/catalog.json"):
            return 200, catalog, {}
    return 200, json.dumps({"ready": True, "managers": [], "packet_status": "advisory",
                            "freshness": {"status": "provisional", "stale": False},
                            "meta": {"stale": False}}).encode(), {"Server-Timing": "ok"}


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
        return fake_success(url)

    monkeypatch.setattr(monitor, "fetch", fetch)
    assert monitor.main() == 0
    assert any(url.endswith("/v1/leagues/58005/live/status") for url in calls)
    assert any(url.endswith("/v1/leagues/131997/live/status") for url in calls)
    assert any("/v1/decision/current?league_id=131997" in url for url in calls)
    assert any(url.endswith("/manifest.json") for url in calls)
    assert any(url.endswith("/v2/league?league=131997") for url in calls)
    assert any(url.endswith("/v2/elite?league=131997") for url in calls)
    assert not any("/v1/leagues/58005/summary" in url for url in calls)


def test_lightweight_monitor_checks_login_without_rendering_heavy_pages(monkeypatch):
    calls = []

    def fetch(url, limit):
        calls.append(url)
        return fake_success(url)

    monkeypatch.setattr(monitor, "fetch", fetch)
    assert monitor.main(lightweight=True) == 0
    assert any(url.endswith("/sign-in") for url in calls)
    assert any(url.endswith("/this-week") for url in calls)
    assert any(url == monitor.SITE + "/" for url in calls)
    assert any(url.endswith("/v2/league?league=131997") for url in calls)
    assert not any(url.endswith(("/v2/compare?league=131997", "/v2/journal")) for url in calls)


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


def test_monitor_rejects_wrong_html_on_an_http_200(monkeypatch):
    monkeypatch.setattr(monitor, "fetch", lambda url, limit: (200, b"Unrelated login shell", {}))
    with pytest.raises(RuntimeError, match="did not render"):
        monitor.check_page("/this-week", "This gameweek")



def test_monitor_rejects_public_catalog_integrity_failure(monkeypatch):
    def fetch(url, limit):
        if url.startswith(monitor.PUBLIC_DATA) and url.endswith("/catalog.json"):
            return 200, b"corrupted snapshot", {}
        return fake_success(url)

    monkeypatch.setattr(monitor, "fetch", fetch)
    with pytest.raises(RuntimeError, match="integrity failed"):
        monitor.main(lightweight=True)


def test_monitor_rejects_stale_public_capture(monkeypatch):
    def fetch(url, limit):
        status, body, headers = fake_success(url)
        if url.endswith("/manifest.json"):
            record = json.loads(body)
            record["generated_at"] = "2020-01-01T00:00:00Z"
            body = json.dumps(record).encode()
        return status, body, headers

    monkeypatch.setattr(monitor, "fetch", fetch)
    with pytest.raises(RuntimeError, match="data stale"):
        monitor.main(lightweight=True)
