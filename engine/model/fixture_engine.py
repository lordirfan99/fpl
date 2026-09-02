"""Fixture utilities for true per-fixture DGW/BGW modelling."""
from collections import defaultdict


def fixtures_by_team_gw(fixtures, gw_ids=None):
    """Return {(gw, team_id): [fixture_view, ...]} preserving ALL fixtures."""
    wanted = set(gw_ids or [])
    out = defaultdict(list)
    for fx in fixtures:
        gw = fx.get("event")
        if gw is None or (wanted and gw not in wanted):
            continue
        h, a = fx.get("team_h"), fx.get("team_a")
        if h:
            out[(gw, h)].append({
                "fixture_id": fx.get("id"), "opponent": a, "home": True,
                "fdr": fx.get("team_h_difficulty", 3), "kickoff_time": fx.get("kickoff_time")})
        if a:
            out[(gw, a)].append({
                "fixture_id": fx.get("id"), "opponent": h, "home": False,
                "fdr": fx.get("team_a_difficulty", 3), "kickoff_time": fx.get("kickoff_time")})
    return dict(out)


def gw_has_published_fixtures(fixture_map, gw):
    return any(k[0] == gw for k in fixture_map)


def fixture_count(fixture_map, gw, team_id):
    return len(fixture_map.get((gw, team_id), []))


def is_dgw(fixture_map, gw, team_id):
    return fixture_count(fixture_map, gw, team_id) >= 2


def is_bgw(fixture_map, gw, team_id):
    return gw_has_published_fixtures(fixture_map, gw) and fixture_count(fixture_map, gw, team_id) == 0
