"""A league snapshot freezes each pick's club. When a player transfers club
afterwards the frozen string misroutes the fixture lookup, the 3-per-club
transfer check and the club shown on the decision board. build_recommendations
must re-stamp every pick from the live catalogue before it reasons about them.
"""
from app.recommendations import (
    _current_team_by_element,
    _normalize_squad_teams,
    build_recommendations,
)

POSITION_TYPE = {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
XI = ["GKP", "DEF", "DEF", "DEF", "MID", "MID", "MID", "MID", "FWD", "FWD", "FWD"]
BENCH = ["GKP", "DEF", "MID", "FWD"]


def _pick(element, name, position, team, cost=5.0, is_captain=False):
    return {
        "element": element, "name": name, "position": position, "team": team,
        "cost": cost, "multiplier": 2 if is_captain else 1, "is_captain": is_captain,
        "is_vice_captain": False,
    }


def _squad(overrides=None):
    overrides = overrides or {}
    squad = []
    for index, position in enumerate(XI + BENCH):
        slot = index + 1
        squad.append(overrides.get(slot) or _pick(100 + slot, f"P{slot}", position, "Gamma"))
    return squad


def _manager(entry_id, overall_rank, squad, *, total_points=50, gw_points=50, gw_bank=20):
    return {
        "entry_id": entry_id, "overall_rank": overall_rank, "squad": squad,
        "total_points": total_points, "gw_points": gw_points, "gw_bank": gw_bank,
        "transfer_details": [],
    }


def _bootstrap(moved_star_to="Beta"):
    teams = [{"id": 1, "name": "Alpha"}, {"id": 2, "name": "Beta"}, {"id": 3, "name": "Gamma"}]
    team_id = {"Alpha": 1, "Beta": 2, "Gamma": 3}
    elements = [
        {"id": 100 + slot, "team": 3, "element_type": POSITION_TYPE[pos], "status": "a",
         "ep_next": 2.0, "form": 2.0, "points_per_game": 2.0, "news": ""}
        for slot, pos in enumerate(XI + BENCH, start=1)
    ]
    # 210 "Star": lives at team `moved_star_to` in the live catalogue.
    elements.append({
        "id": 210, "team": team_id[moved_star_to], "element_type": POSITION_TYPE["MID"],
        "status": "a", "ep_next": 9.0, "form": 8.0, "points_per_game": 8.0, "news": "",
    })
    return {"teams": teams, "elements": elements}


def _fixtures():
    # Beta at home to Gamma; Alpha away at Gamma. A stale "Alpha" label would
    # resolve to the Alpha fixture, the live "Beta" label to the Beta one.
    return [
        {"team_h": "Beta", "team_a": "Gamma", "team_h_difficulty": 2, "team_a_difficulty": 4},
        {"team_h": "Gamma", "team_a": "Alpha", "team_h_difficulty": 3, "team_a_difficulty": 3},
    ]


def test_current_team_by_element_maps_ids_to_club_names():
    mapping = _current_team_by_element(_bootstrap())
    assert mapping[210] == "Beta"
    assert mapping[101] == "Gamma"


def test_normalize_rewrites_only_stale_picks_without_mutating_input():
    current = {1: "Beta", 2: "Gamma"}
    original = [{"entry_id": 7, "squad": [
        {"element": 1, "team": "Alpha"},   # stale -> Beta
        {"element": 2, "team": "Gamma"},   # already correct
        {"element": 3, "team": "Delta"},   # unknown id -> untouched
    ]}]
    patched = _normalize_squad_teams(original, current)
    assert [p["team"] for p in patched[0]["squad"]] == ["Beta", "Gamma", "Delta"]
    assert original[0]["squad"][0]["team"] == "Alpha"  # input untouched


def test_decision_board_shows_the_current_club_and_fixture():
    star_stale = _pick(210, "Star", "MID", "Alpha", cost=8.0)   # snapshot froze the old club
    squad = _squad({5: star_stale})
    managers = [
        _manager(2797967, 10, squad, total_points=90),
        _manager(2, 1, _squad({5: _pick(210, "Star", "MID", "Alpha", cost=8.0, is_captain=True)}),
                 total_points=140),
        _manager(3, 2, _squad({5: _pick(210, "Star", "MID", "Alpha", cost=8.0)}), total_points=120),
    ]
    result = build_recommendations(
        managers[0], managers, _bootstrap(moved_star_to="Beta"), _fixtures(),
        population_size=3, gameweek=3,
    )
    template = {row["element"]: row for row in result["competitive"]["elite_template"]}
    assert template[210]["team"] == "Beta"
    star_signal = next(pick for pick in result["captains"] if pick["element"] == 210)
    assert star_signal["team"] == "Beta"
    assert star_signal["fixture"].startswith("Gamma")   # Beta's fixture, not Alpha's


def _club_limit_managers():
    # Manager owns three Beta players (GK, DEF, FWD) plus a cheap, weak Gamma
    # midfielder as the obvious sell. "Star" (210) is the elite target.
    squad = _squad({
        1: _pick(301, "BGK", "GKP", "Beta"),
        2: _pick(302, "BDEF", "DEF", "Beta"),
        9: _pick(304, "BFWD", "FWD", "Beta"),
        5: _pick(105, "WeakMid", "MID", "Gamma", cost=5.0),
    })
    return [
        _manager(2797967, 10, squad, total_points=90, gw_bank=40),
        _manager(2, 1, _squad({5: _pick(210, "Star", "MID", "Alpha", cost=8.0)}), total_points=140),
        _manager(3, 2, _squad({5: _pick(210, "Star", "MID", "Alpha", cost=8.0)}), total_points=120),
    ]


def _club_limit_bootstrap(star_club):
    boot = _bootstrap(moved_star_to=star_club)
    boot["elements"].extend([
        {"id": 301, "team": 2, "element_type": 1, "status": "a", "ep_next": 2, "form": 2,
         "points_per_game": 2, "news": ""},
        {"id": 302, "team": 2, "element_type": 2, "status": "a", "ep_next": 2, "form": 2,
         "points_per_game": 2, "news": ""},
        {"id": 304, "team": 2, "element_type": 4, "status": "a", "ep_next": 2, "form": 2,
         "points_per_game": 2, "news": ""},
    ])
    return boot


def test_club_limit_blocks_a_transfer_that_is_really_a_fourth_club_pick():
    # Snapshot still files Star under Alpha; the live catalogue has moved him to
    # Beta, where the manager already has three. The move must be rejected.
    result = build_recommendations(
        _club_limit_managers()[0], _club_limit_managers(),
        _club_limit_bootstrap("Beta"), _fixtures(), population_size=3, gameweek=3,
    )
    assert 210 not in {move["incoming"]["element"] for move in result["transfers"]}


def test_same_transfer_is_allowed_when_the_player_has_not_changed_club():
    # Identical scenario, but Star genuinely still plays for Alpha -> only the
    # club count differs, proving the block above comes from the club fix.
    result = build_recommendations(
        _club_limit_managers()[0], _club_limit_managers(),
        _club_limit_bootstrap("Alpha"), _fixtures(), population_size=3, gameweek=3,
    )
    assert 210 in {move["incoming"]["element"] for move in result["transfers"]}
