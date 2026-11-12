import importlib.util
import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, call
from urllib.error import HTTPError, URLError
from urllib.response import addinfourl

import pytest


SPEC = importlib.util.spec_from_file_location(
    "monitor_live_test", Path(__file__).resolve().parents[2] / "infra/scripts/monitor_production.py"
)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


@pytest.mark.parametrize("failure", ["http", "read_timeout", "connect_timeout"])
def test_fetch_retries_temporary_service_failures(monkeypatch, failure):
    url = f"{monitor.API}/v1/leagues/131997/live/status"
    body = b'{"ready":true}'
    if failure == "http":
        errors = [HTTPError(url, 503, "Service Unavailable", {}, BytesIO()) for _ in range(2)]
    elif failure == "read_timeout":
        errors = [TimeoutError("read timed out")]
    else:
        errors = [URLError(TimeoutError("connection timed out"))]
    urlopen = Mock(side_effect=[*errors, addinfourl(BytesIO(body), {}, url, 200)])
    sleep = Mock()
    monkeypatch.setattr(monitor, "urlopen", urlopen)
    monkeypatch.setattr(monitor, "sleep", sleep)

    assert monitor.fetch(url, 50_000) == (200, body, {})
    assert urlopen.call_count == len(errors) + 1
    assert sleep.call_args_list == [call(2), call(32)][:len(errors)]


@pytest.mark.parametrize("status,attempts", [(503, 3), (404, 1)])
def test_fetch_reports_endpoint_and_request_id_on_failure(monkeypatch, status, attempts):
    url = f"{monitor.API}/v1/leagues/58005/live/status"
    headers = {"X-Request-ID": "test-request"}
    urlopen = Mock(side_effect=[HTTPError(url, status, "error", headers, BytesIO()) for _ in range(attempts)])
    sleep = Mock()
    monkeypatch.setattr(monitor, "urlopen", urlopen)
    monkeypatch.setattr(monitor, "sleep", sleep)

    with pytest.raises(HTTPError, match="test-request") as failure:
        monitor.fetch(url, 50_000)
    assert failure.value.code == status
    assert failure.value.__notes__ == [f"GET {url} (request ID test-request)"]
    assert urlopen.call_count == attempts
    assert sleep.call_count == attempts - 1


def fake_success(url):
    pages = {
        "/": "This gameweek",
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
            for league_id in (58005, 131997):
                files[f"leagues/{league_id}/live.json"] = hashlib.sha256(b'{"league":"fixture"}').hexdigest()
            return 200, json.dumps({
                "schema_version": 1, "release": "f" * 64, "files": files,
                "writes_enabled": False, "generated_at": datetime.now(timezone.utc).isoformat()
            }).encode(), {}
        if url.endswith("/catalog.json"):
            return 200, catalog, {}
        if url.endswith("/live.json"):
            return 200, b'{"league":"fixture"}', {}
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


def test_monitor_rejects_v2_homepage_after_v1_restore(monkeypatch):
    def fetch(url, limit):
        if url == monitor.SITE + "/":
            return 200, b"Your next FPL decision", {}
        return fake_success(url)

    monkeypatch.setattr(monitor, "fetch", fetch)
    with pytest.raises(RuntimeError, match="Dashboard route / did not render"):
        monitor.main(lightweight=True)


def test_monitor_follows_streamed_homepage_redirect(monkeypatch):
    calls = []

    def fetch(url, limit):
        calls.append(url)
        if url == monitor.SITE + "/":
            return 200, b'<meta http-equiv="refresh" content="1;url=/this-week"/>', {}
        return fake_success(url)

    monkeypatch.setattr(monitor, "fetch", fetch)
    assert monitor.main(lightweight=True) == 0
    assert monitor.SITE + "/this-week" in calls


@pytest.mark.parametrize("destination", ["/sign-in", "https://example.com/this-week", "/this-week?error=1"])
def test_monitor_rejects_unexpected_homepage_redirect(monkeypatch, destination):
    html = f'<meta http-equiv="refresh" content="1;url={destination}"/>'.encode()
    monkeypatch.setattr(monitor, "fetch", lambda url, limit: (200, html, {}))
    with pytest.raises(RuntimeError, match="did not render"):
        monitor.check_page("/", "This gameweek", home_redirect=True)


def test_monitor_rejects_broken_redirect_destination(monkeypatch):
    def fetch(url, limit):
        if url == monitor.SITE + "/":
            return 200, b'<meta http-equiv="refresh" content="1;url=/this-week"/>', {}
        return 200, b"We could not load this view.", {}

    monkeypatch.setattr(monitor, "fetch", fetch)
    with pytest.raises(RuntimeError, match="Dashboard route /this-week did not render"):
        monitor.check_page("/", "This gameweek", home_redirect=True)
