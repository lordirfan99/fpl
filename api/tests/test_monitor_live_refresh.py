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
        return 200, json.dumps({"ready": True, "managers": []}).encode(), {"server-timing": "ok"}

    monkeypatch.setattr(monitor, "fetch", fetch)
    assert monitor.main() == 0
    assert any(url.endswith("/v1/leagues/58005/live/status") for url in calls)
    assert any(url.endswith("/v1/leagues/131997/live/status") for url in calls)
