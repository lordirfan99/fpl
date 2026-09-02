import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fetch_fixture_horizon.py"
SPEC = importlib.util.spec_from_file_location("fetch_fixture_horizon", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def official_shape() -> tuple[dict, list[dict]]:
    bootstrap = {
        "elements": [{"id": index, "web_name": f"Player {index}"} for index in range(1, 401)],
        "teams": [{"id": index, "name": f"Team {index}"} for index in range(1, 21)],
        "events": [{"id": index} for index in range(1, 39)],
        "element_types": [{"id": index} for index in range(1, 5)],
    }
    fixtures = [
        {"id": index, "team_h": index % 20 + 1, "team_a": (index + 1) % 20 + 1,
         "team_h_difficulty": 3, "team_a_difficulty": 3}
        for index in range(300)
    ]
    return bootstrap, fixtures


def test_verified_official_shape_is_accepted() -> None:
    bootstrap, fixtures = official_shape()
    MODULE.validate_official_payload(bootstrap, fixtures)


@pytest.mark.parametrize("mutation", ["players", "teams", "events", "fixtures", "difficulty"])
def test_placeholder_or_incomplete_payload_is_rejected(mutation: str) -> None:
    bootstrap, fixtures = official_shape()
    if mutation == "players": bootstrap["elements"] = []
    if mutation == "teams": bootstrap["teams"] = bootstrap["teams"][:2]
    if mutation == "events": bootstrap["events"] = bootstrap["events"][:1]
    if mutation == "fixtures": fixtures = fixtures[:2]
    if mutation == "difficulty": fixtures[0]["team_h_difficulty"] = 0
    with pytest.raises(RuntimeError):
        MODULE.validate_official_payload(bootstrap, fixtures)
