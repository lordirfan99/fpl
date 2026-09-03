"""v4.0 scoring: fixture strength must actually move the projection, and bonus
must track projected returns instead of a flat per-position rate."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))

from component_xpts import fixture_xpts  # noqa: E402


def _fwd(**overrides):
    el = {
        "element_type": 4, "status": "a", "chance_of_playing_next_round": None,
        "minutes": 810, "goals_scored": 6, "assists": 3, "bonus": 8,
        "expected_goals": 5.4, "expected_assists": 2.6, "saves": 0,
        "clean_sheets": 0, "defensive_contribution": 0,
        "yellow_cards": 1, "red_cards": 0,
    }
    el.update(overrides)
    return el


def _blank_fwd():
    # plays every week, never returns anything
    return _fwd(goals_scored=0, assists=0, bonus=0, expected_goals=0.1,
               expected_assists=0.1)


GW = 9


def test_fixture_strength_swings_attacking_output_by_roughly_20pct():
    easy = fixture_xpts(_fwd(), {"fdr": 1}, GW)
    hard = fixture_xpts(_fwd(), {"fdr": 5}, GW)
    avg = fixture_xpts(_fwd(), {"fdr": 3}, GW)

    easy_att = easy.components["goals"] + easy.components["assists"]
    hard_att = hard.components["goals"] + hard.components["assists"]
    avg_att = avg.components["goals"] + avg.components["assists"]

    assert easy_att > avg_att > hard_att
    # old band was +/-13%; now the easy vs hard spread should exceed 35%
    assert (easy_att - hard_att) / avg_att > 0.35
    # but still bounded - not a runaway multiplier
    assert easy_att / avg_att < 1.30


def test_bonus_tracks_projected_returns_and_responds_to_fixture():
    hauler = fixture_xpts(_fwd(), {"fdr": 2}, GW).components["bonus"]
    blank = fixture_xpts(_blank_fwd(), {"fdr": 2}, GW).components["bonus"]
    # bonus now scales with THIS projection's returns, not just the player's
    # own historical bonus rate
    assert hauler > blank * 1.7
    assert hauler - blank > 0.30
    assert hauler < 2.6                              # capped below the 3-pt max

    # and it is forward-looking: a better fixture lifts the same player's bonus
    easy = fixture_xpts(_fwd(), {"fdr": 1}, GW).components["bonus"]
    hard = fixture_xpts(_fwd(), {"fdr": 5}, GW).components["bonus"]
    assert easy > hard


def test_clean_sheet_defender_earns_bonus_from_the_cs_component():
    defender = _fwd(element_type=2, goals_scored=1, assists=1, expected_goals=0.8,
                    expected_assists=0.9, clean_sheets=5, defensive_contribution=95)
    strong_cs = fixture_xpts(defender, {"fdr": 1}, GW)
    weak_cs = fixture_xpts(defender, {"fdr": 5}, GW)
    assert strong_cs.components["clean_sheet"] > weak_cs.components["clean_sheet"]
    assert strong_cs.components["bonus"] > weak_cs.components["bonus"]
