"""The read API stamps a Cache-Control policy tiered by data volatility."""
import pytest
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

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
    ("/v1/optimizer/transfers", main._SNAPSHOT),
    ("/v1/leagues/registry", main._SNAPSHOT),
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


def test_reference_policies_are_public_with_a_swr_window():
    for policy in (main._REFERENCE_LONG, main._REFERENCE_DAY, main._LIVE_SHORT, main._SNAPSHOT):
        assert policy.startswith("public, max-age=")
        assert "stale-while-revalidate=" in policy


def test_health_and_readiness_are_never_publicly_cached():
    # /health is always 200; /ready is 503 when snapshot data is absent (CI).
    # Both must carry no-store regardless of status.
    for path in ("/health", "/ready"):
        assert CLIENT.get(path).headers["cache-control"] == "no-store"


def test_non_200_responses_fall_back_to_no_store():
    @main.app.get("/v1/__cache_probe_404")
    def _missing():  # pragma: no cover - registered only for this test
        raise main.HTTPException(status_code=404, detail="nope")

    assert CLIENT.get("/v1/__cache_probe_404").headers["cache-control"] == "no-store"


def test_a_handler_that_sets_its_own_cache_control_is_not_overridden():
    @main.app.get("/v1/__cache_probe_own")
    def _own():  # pragma: no cover - registered only for this test
        return PlainTextResponse("ok", headers={"Cache-Control": "private, max-age=1"})

    assert CLIENT.get("/v1/__cache_probe_own").headers["cache-control"] == "private, max-age=1"
