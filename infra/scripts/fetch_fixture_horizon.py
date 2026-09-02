"""Refresh the full FPL fixture horizon used by the dashboard planner."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
FPL_BASE = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "FantasyScoutControlCentre/1.0"}


def get_json(path: str):
    request = Request(f"{FPL_BASE}/{path}", headers=HEADERS)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


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
    if any(not row.get("web_name") or row.get("id") is None for row in players):
        raise RuntimeError("Official player catalogue contains an unnamed or unkeyed row")


def main() -> None:
    bootstrap = get_json("bootstrap-static/")
    fixtures = get_json("fixtures/")
    validate_official_payload(bootstrap, fixtures)
    fetched_at = datetime.now(timezone.utc).isoformat()
    bootstrap_hash = hashlib.sha256(
        json.dumps(bootstrap, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    bootstrap["_meta"] = {
        "fetched_at": fetched_at,
        "source": "official-fpl-api/bootstrap-static",
        "content_sha256": bootstrap_hash,
    }
    teams = {team["id"]: team["name"] for team in bootstrap["teams"]}
    gameweeks: dict[str, list[dict]] = {str(gw): [] for gw in range(1, 39)}

    for fixture in fixtures:
        event = fixture.get("event")
        if not event:
            continue
        gameweeks[str(event)].append(
            {
                "event": event,
                "team_h": teams[fixture["team_h"]],
                "team_a": teams[fixture["team_a"]],
                "team_h_difficulty": fixture.get("team_h_difficulty", 0),
                "team_a_difficulty": fixture.get("team_a_difficulty", 0),
                "kickoff_time": fixture.get("kickoff_time"),
            }
        )

    payload = {
        "schema_version": 2,
        "fetched_at": fetched_at,
        "source": "official-fpl-api",
        "source_url": f"{FPL_BASE}/fixtures/",
        "content_sha256": hashlib.sha256(
            json.dumps(fixtures, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "fixture_count": len(fixtures),
        "gameweeks": gameweeks,
    }
    target = DATA_DIR / "fixtures_cache.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (DATA_DIR / "bootstrap_cache.json").write_text(
        json.dumps(bootstrap, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )
    populated = sum(bool(items) for items in gameweeks.values())
    print(f"Saved {len(fixtures)} fixtures across {populated} gameweeks and refreshed bootstrap provenance")


if __name__ == "__main__":
    main()
