"""Behavioral regressions for stale hauls, healthy blanks and legal transfers."""
from copy import deepcopy

import pytest

from backfill_player_history import backfill
from feature_store_v42 import load_event_history
from horizon_milp import optimize_horizon
from recent_form import attacking_form
from v4_projection import project_player
from v42_projection import project_player_v42


def bruno_history():
    # Regression: Bruno Fernandes (426), GW1–6. The old GW2 haul supplied
    # 23/32 points and 2.50/4.04 xGI; the last three GWs supplied only 0.99 xGI.
    points = [2, 23, 2, 2, 2, 1]
    xg = [0.08, 2.02, 0.15, 0.03, 0.28, 0.02]
    xa = [0.26, 0.48, 0.06, 0.33, 0.19, 0.14]
    return [{"gw": gw, "element": 426, "minutes": 90, "starts": 1,
             "goals_scored": 3 if gw == 2 else 0,
             "assists": 1 if gw == 2 else 0,
             "expected_goals": xg[gw - 1], "expected_assists": xa[gw - 1],
             "total_points": points[gw - 1]}
            for gw in range(1, 7)]


def element(rows):
    return {"id": 426, "team": 1, "element_type": 3, "now_cost": 119,
            "status": "a", "minutes": sum(row["minutes"] for row in rows),
            "starts": len(rows), "ep_next": "1.5",
            **{field: sum(row[field] for row in rows) for field in
               ("goals_scored", "assists", "expected_goals", "expected_assists")}}


FIXTURES = {(gw, 1): [{"fdr": 3, "opponent": 2, "home": True}] for gw in (7, 8, 9)}


def test_stale_haul_lowers_the_full_forecast_and_exposes_evidence():
    history = bruno_history()
    player = element(history)
    before = project_player(player, FIXTURES, 6, [7, 8, 9])
    after = project_player(player, FIXTURES, 6, [7, 8, 9], history=history)
    assert all(new < old for new, old in zip(after["xpts_by_gw"], before["xpts_by_gw"]))
    assert after["xpts_horizon"] < before["xpts_horizon"]
    assert after["expected_minutes"] == before["expected_minutes"]
    assert after["recent_form"]["single_haul_dependency"]
    assert after["recent_form"]["best_points_gw"] == 2


def test_same_season_totals_favour_a_player_with_improving_recent_evidence():
    declining = bruno_history()
    improving = [dict(row, gw=7 - row["gw"]) for row in declining]
    old = project_player(element(declining), FIXTURES, 6, [7, 8, 9], history=declining)
    new = project_player(element(improving), FIXTURES, 6, [7, 8, 9], history=improving)
    assert new["expected_horizon"] > old["expected_horizon"]


def test_blank_points_with_healthy_underlying_numbers_do_not_reduce_mean():
    healthy = [dict(row, goals_scored=0, assists=0, expected_goals=0.5,
                    expected_assists=0.3, total_points=2) for row in bruno_history()]
    one_haul = [dict(row, total_points=23 if row["gw"] == 2 else 1) for row in healthy]
    base = project_player(element(healthy), FIXTURES, 6, [7, 8, 9])
    blanks = project_player(element(healthy), FIXTURES, 6, [7, 8, 9], history=healthy)
    haul = project_player(element(healthy), FIXTURES, 6, [7, 8, 9], history=one_haul)
    assert blanks["xpts_by_gw"] == base["xpts_by_gw"] == haul["xpts_by_gw"]
    assert not haul["recent_form"]["single_haul_dependency"]


def test_future_rows_and_input_order_cannot_change_forecast_or_mutate_history():
    history = bruno_history()
    original = deepcopy(history)
    future = dict(history[1], gw=7, goals_scored=99, expected_goals=99)
    before = project_player(element(history), FIXTURES, 6, [7, 8, 9], history=history)
    after = project_player(element(history), FIXTURES, 6, [7, 8, 9],
                           history=list(reversed(history + [future])))
    assert before == after
    assert original == history


@pytest.mark.parametrize("modify", [
    lambda rows: rows[:2],
    lambda rows: rows[:-1],  # Stale history: latest completed GW missing.
    lambda rows: [row for row in rows if row["gw"] != 5],
    lambda rows: [dict(row, expected_goals=None) for row in rows],
    lambda rows: [dict(row, expected_assists=float("nan")) for row in rows],
    lambda rows: [dict(row, minutes=10) for row in rows],
])
def test_sparse_missing_or_invalid_data_retains_the_season_fallback(modify):
    rows = bruno_history()
    baseline = project_player(element(rows), FIXTURES, 6, [7, 8, 9])
    fallback = project_player(element(rows), FIXTURES, 6, [7, 8, 9], history=modify(rows))
    assert fallback["xpts_by_gw"] == baseline["xpts_by_gw"]
    assert fallback["recent_form"]["status"] != "applied"


def test_double_gameweek_minutes_are_exposure_not_two_independent_gameweeks():
    rows = [dict(row, minutes=180, expected_goals=0.8, expected_assists=0.4,
                 goals_scored=0, assists=0) for row in bruno_history()]
    rates, _ = attacking_form(rows, 7)
    assert rates["expected_goals"] == pytest.approx(0.4)
    assert rates["expected_assists"] == pytest.approx(0.2)


def test_v42_uses_the_same_recent_attack_signal():
    rows = bruno_history()
    improving = [dict(row, gw=7 - row["gw"]) for row in rows]
    before = project_player_v42(element(rows), FIXTURES, [7, 8, 9], {426: rows}, {}, {})
    after = project_player_v42(element(rows), FIXTURES, [7, 8, 9], {426: improving}, {}, {})
    assert after["expected_horizon"] > before["expected_horizon"]
    assert before["recent_form"]["single_haul_dependency"]


def test_only_data_checked_events_are_backfilled_and_reruns_are_idempotent(tmp_path):
    bootstrap = {"elements": [{"id": 426, "team": 1, "element_type": 3}],
                 "events": [{"id": 1, "finished": True, "data_checked": True},
                            {"id": 2, "finished": True, "data_checked": False},
                            {"id": 3, "finished": False, "data_checked": False}]}
    calls = []

    def live(gw):
        calls.append(gw)
        return {"elements": [{"id": 426, "stats": bruno_history()[0]}]}

    path = str(tmp_path / "history.jsonl")
    assert backfill(path, bootstrap, live) == [1]
    assert backfill(path, bootstrap, live) == []
    assert calls == [1]
    assert len(load_event_history(path)) == 1


def test_downgrade_and_upgrade_package_can_release_premium_budget_without_a_hit():
    """The existing optimizer must spend freed cash on the whole XI, not PPM."""
    def player(pid, pos, cost, points):
        return {"id": pid, "name": str(pid), "position": pos, "club": pid,
                "cost": cost, "selling_price": cost, "xpts": points,
                "xpts_by_gw": [points] * 3, "variance_by_gw": [0] * 3,
                "p_start": 1.0, "expected_minutes": 90}

    squad = ([player(1, "GKP", 45, 4), player(2, "GKP", 40, 0)]
             + [player(i, "DEF", 45, 3) for i in range(3, 8)]
             + [player(8, "MID", 119, 4)]
             + [player(i, "MID", 55, 4) for i in range(9, 13)]
             + [player(i, "FWD", 60, 5) for i in range(13, 16)])
    cheap_mid = player(100, "MID", 70, 3.8)
    upgrade = player(101, "DEF", 90, 7)
    result = optimize_horizon(squad, squad + [cheap_mid, upgrade], 0, 2,
                              max_paid_transfers=0, horizon=3)
    first = result["weeks"][0]
    pairs = {(row["element_out"], row["element_in"]) for row in first["transfers"]}
    assert (8, 100) in pairs
    assert any(incoming == 101 for _, incoming in pairs)
    assert first["bank_after"] >= 0
    assert first["hits"] == 0
