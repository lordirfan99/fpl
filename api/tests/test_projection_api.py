from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_lab_projection_api_is_separate_and_catalog_wide() -> None:
    response = client.get("/v1/projections/current?gw=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["projection_version"] == "projection-v5.0-lab"
    assert payload["meta"]["source"] == "projection-v5-lab"
    assert len(payload["players"]) == len(client.get("/v1/catalog").json()["players"])
    assert "elite_ownership" not in payload["players"][0]
    assert payload["players"][0]["uncertainty"]["status"] == "heuristic_not_calibrated"
