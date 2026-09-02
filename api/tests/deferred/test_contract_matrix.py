from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_public_read_contract_matrix_has_no_unexpected_server_errors() -> None:
    cases = {
        "/health": 200,
        "/ready": 200,
        "/v1/me": 200,
        "/v1/catalog": 200,
        "/v1/catalog/compact": 200,
        "/v1/fixtures?from_gw=1&to_gw=10": 200,
        "/v1/leagues/58005?gw=1": 200,
        "/v1/leagues/58005/summary?gw=1&page=1&page_size=50": 200,
        "/v1/leagues/58005/directory?gw=1": 200,
        "/v1/leagues/58005/managers/2797967?gw=1": 200,
        "/v1/me/team?league_id=58005&gw=1": 200,
        "/v1/elite/1?league_id=58005": 200,
        "/v1/recommendations/current?league_id=58005&gw=1": 200,
        "/v1/projections/current?gw=1": 200,
        "/v1/optimizer/transfers?league_id=58005&gw=1&horizon=2&max_transfers=1": 200,
        "/v1/journal?season=2026-27": 200,
        "/v1/journal/2026-27/gw/1": 200,
        "/v1/fixtures?from_gw=10&to_gw=1": 400,
        "/v1/leagues/58005?gw=38": 404,
    }
    for path, expected in cases.items():
        response = client.get(path)
        assert response.status_code == expected, (path, response.status_code, response.text[:300])


def test_compact_contracts_exclude_squads_and_publish_provenance() -> None:
    full = client.get("/v1/leagues/58005?gw=1").content
    summary_response = client.get("/v1/leagues/58005/summary?gw=1&page=1&page_size=50")
    summary = summary_response.json()
    assert len(summary_response.content) < len(full) * 0.1
    assert len(summary["managers"]) == 50
    assert all("squad" not in manager for manager in summary["managers"])
    assert len(summary["meta"]["data_hash"]) == 64
    assert summary_response.headers["server-timing"].startswith("app;dur=")
    directory = client.get("/v1/leagues/58005/directory?gw=1").json()
    assert directory["count"] == 1218
    assert all("squad" not in manager for manager in directory["managers"])
    detail = client.get("/v1/leagues/58005/managers/2797967?gw=1").json()
    assert len(detail["squad"]) == 15
    projection = client.get("/v1/projections/current?gw=1").json()
    assert projection["meta"]["schema_version"] == "api-meta-v2"
    assert projection["meta"]["model_version"] == "projection-v5.0-lab"
    assert projection["meta"]["data_hash"]


def test_health_and_optimizer_are_read_only_and_revision_aware() -> None:
    health = client.get("/health").json()
    assert "revision" in health
    assert health["readiness"]["ready"] is True
    optimizer = client.get("/v1/optimizer/transfers?league_id=58005&gw=1&horizon=2&max_transfers=1").json()
    assert optimizer["optimizer_version"] == "net-ev-multiweek-v1"
    assert optimizer["execution_authority"] == "manual_fpl"
    assert optimizer["writes_enabled"] is False
    assert optimizer["meta"]["model_version"] == "net-ev-multiweek-v1"


def test_all_gameweek_fixture_contracts_preserve_the_requested_week() -> None:
    for gameweek in range(1, 39):
        payload = client.get(f"/v1/fixtures?from_gw={gameweek}&to_gw={gameweek}").json()
        assert payload["from_gameweek"] == payload["to_gameweek"] == gameweek
        assert str(gameweek) in payload["gameweeks"]

