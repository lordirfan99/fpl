import copy

import pytest

from app.reference_snapshots import reference_payloads


def references():
    bootstrap = {"elements": [{"id": i, "web_name": f"Player {i}"} for i in range(1, 401)],
                 "teams": [{"id": i, "name": f"Club {i}"} for i in range(1, 21)],
                 "events": [{"id": i} for i in range(1, 39)], "element_types": [{"id": i} for i in range(1, 5)]}
    fixtures = [{"id": i, "event": (i % 38) + 1, "team_h": 1, "team_a": 2,
                 "team_h_difficulty": 2, "team_a_difficulty": 3} for i in range(1, 381)]
    return bootstrap, fixtures


@pytest.mark.parametrize("defect", ["players", "teams", "events", "positions", "fixtures", "malformed", "duplicate_player", "duplicate_team", "invalid_event"])
def test_partial_or_malformed_official_data_is_rejected(defect):
    bootstrap, fixtures = references()
    if defect in ("players", "teams", "events", "positions"):
        bootstrap[{"players": "elements", "teams": "teams", "events": "events", "positions": "element_types"}[defect]].pop()
    elif defect == "fixtures":
        fixtures = fixtures[:299]
    elif defect == "malformed":
        fixtures[0]["team_a"] = 999
    elif defect == "duplicate_player":
        bootstrap["elements"][1] = copy.deepcopy(bootstrap["elements"][0])
    elif defect == "duplicate_team":
        bootstrap["teams"][1] = copy.deepcopy(bootstrap["teams"][0])
    else:
        fixtures[0]["event"] = 99
    with pytest.raises(RuntimeError):
        reference_payloads(bootstrap, fixtures, "2026-10-14T08:00:00Z")
