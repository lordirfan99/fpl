"""Public reference contract shared with the background collector."""
import hashlib
import json

def validate_official_payload(bootstrap: object, fixtures: object) -> None:
    """Reject empty, partial or synthetic-looking payloads before publication."""
    if not isinstance(bootstrap, dict) or not isinstance(fixtures, list):
        raise RuntimeError("Official FPL payload has the wrong top-level shape")
    players = bootstrap.get("elements")
    teams = bootstrap.get("teams")
    events = bootstrap.get("events")
    positions = bootstrap.get("element_types")
    if not isinstance(players, list) or len(players) < 400:
        raise RuntimeError(f"Official player catalogue is implausibly small: {len(players or [])}")
    if not isinstance(teams, list) or len(teams) != 20:
        raise RuntimeError(f"Official team catalogue must contain 20 teams: {len(teams or [])}")
    if not isinstance(events, list) or len(events) != 38:
        raise RuntimeError(f"Official event catalogue must contain 38 gameweeks: {len(events or [])}")
    if not isinstance(positions, list) or len(positions) != 4:
        raise RuntimeError("Official position catalogue must contain four positions")
    if len(fixtures) < 300:
        raise RuntimeError(f"Official fixture catalogue is implausibly small: {len(fixtures)}")
    team_ids = {team.get("id") for team in teams if isinstance(team, dict)}
    if len(team_ids) != 20 or any(not isinstance(row, dict) or not row.get("name") for row in teams):
        raise RuntimeError("Official team identities are incomplete")
    if {row.get("id") for row in events if isinstance(row, dict)} != set(range(1, 39)):
        raise RuntimeError("Official event identities are incomplete")
    if any(
        not isinstance(row, dict)
        or row.get("id") is None
        or row.get("team_h") not in team_ids
        or row.get("team_a") not in team_ids
        or row.get("team_h_difficulty") not in range(1, 6)
        or row.get("team_a_difficulty") not in range(1, 6)
        for row in fixtures
    ):
        raise RuntimeError("Official fixture catalogue contains an invalid or incomplete row")
    if any(not isinstance(row, dict) or not row.get("web_name") or row.get("id") is None for row in players):
        raise RuntimeError("Official player catalogue contains an unnamed or unkeyed row")
    if len({row["id"] for row in players}) != len(players):
        raise RuntimeError("Official player identities are duplicated")

def reference_payloads(bootstrap, fixtures, captured):
    validate_official_payload(bootstrap, fixtures)
    def digest(payload):
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    catalog = {**bootstrap, "_meta": {"fetched_at": captured, "source": "official-fpl-api/bootstrap-static",
                                     "content_sha256": digest(bootstrap)}}
    teams = {row["id"]: row["name"] for row in bootstrap["teams"]}
    gameweeks = {str(gw): [] for gw in range(1, 39)}
    for fixture in fixtures:
        event = fixture.get("event")
        if event is None:
            continue
        if str(event) not in gameweeks:
            raise RuntimeError("Fixture references an invalid gameweek")
        gameweeks[str(event)].append({
            "event": event, "team_h": teams[fixture["team_h"]], "team_a": teams[fixture["team_a"]],
            "team_h_difficulty": fixture["team_h_difficulty"], "team_a_difficulty": fixture["team_a_difficulty"],
            "kickoff_time": fixture.get("kickoff_time"),
        })
    horizon = {"schema_version": 2, "fetched_at": captured, "source": "official-fpl-api",
               "source_url": "https://fantasy.premierleague.com/api/fixtures/", "content_sha256": digest(fixtures),
               "fixture_count": len(fixtures), "gameweeks": gameweeks}
    return catalog, horizon
