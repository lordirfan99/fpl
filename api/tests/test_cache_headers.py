"""The read API stamps a Cache-Control policy tiered by data volatility."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import _cache_policy

CLIENT = TestClient(main.app)


@pytest.mark.parametrize("path,expected", [
    ("/v1/catalog", main._REFERENCE_LONG),
    ("/v1/catalog/compact", main._REFERENCE_LONG),
    ("/v1/fixtures", main._REFERENCE_DAY),
    ("/v1/journal", main._REFERENCE_DAY),
    ("/v1/journal/2026-27/gw/2", main._REFERENCE_DAY),
    ("/v1/leagues/58005/live/status", main._LIVE_SHORT),
    ("/v1/live/team", main._LIVE_SHORT),
    ("/v1/leagues/58005/summary", main._SNAPSHOT),
    ("/v1/elite/2", main._SNAPSHOT),
    ("/v1/projections/current", main._SNAPSHOT),
    ("/health", "no-store"),
    ("/ready", "no-store"),
    ("/v1/me", "no-store"),
    ("/v1/me/team", "no-store"),
    ("/v1/recommendations/current", "no-store"),
    ("/v1/decision/current", "no-store"),
    ("/v1/private/dashboard/current", "no-store"),
])
def test_cache_policy_matrix(path, expected):
    assert _cache_policy(path) == expected


def test_no_store_endpoints_are_never_publicly_cached():
    for path in ("/health", "/ready"):
        assert CLIENT.get(path).headers["cache-control"] == "no-store"


def test_reference_endpoint_advertises_a_public_swr_window():
    response = CLIENT.get("/v1/catalog/compact")
    assert response.status_code == 200
    cache_control = response.headers["cache-control"]
    assert cache_control.startswith("public, max-age=")
    assert "stale-while-revalidate=" in cache_control


def test_a_handler_that_sets_its_own_cache_control_is_not_overridden(monkeypatch):
    # The private dashboard router stamps `private, no-store`; the middleware
    # must leave any pre-set Cache-Control alone.
    from starlette.responses import PlainTextResponse

    @main.app.get("/v1/__cache_probe")
    def _probe():  # pragma: no cover - registered only for this test
        return PlainTextResponse("ok", headers={"Cache-Control": "private, max-age=1"})

    response = CLIENT.get("/v1/__cache_probe")
    assert response.headers["cache-control"] == "private, max-age=1"
