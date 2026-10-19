from competitive_v44_candidate import _state_probs, predict_gameweek, predict_player
from evaluate_v44_candidate import _promotion_gate, _prospective_baselines


def row(gw, element=1, minutes=90, points=6, xg=0.0, xa=0.0, position=2, team=1):
    return {"gw": gw, "element": element, "position": position, "team": team, "minutes": minutes, "starts": int(minutes >= 60),
            "total_points": points, "expected_goals": xg, "expected_assists": xa, "goals_scored": 0, "assists": 0,
            "bonus": 0, "defensive_contribution": 0, "clean_sheets": 0, "saves": 0, "yellow_cards": 0, "red_cards": 0}


def test_all_dnp_history_reduces_start_probability():
    dnp = _state_probs([row(gw, minutes=0) for gw in range(1, 7)], "DEF")
    starter = _state_probs([row(gw, minutes=90) for gw in range(1, 7)], "DEF")
    assert dnp[2] < 0.68
    assert starter[2] > dnp[2]


def test_future_rows_cannot_change_prediction_or_universe():
    prior = [row(1, element=1, points=4)]
    base = predict_gameweek(prior, 2)
    with_future = predict_gameweek(prior + [row(2, element=99, points=40, xg=4), row(3, element=1, points=40, xg=4)], 2)
    assert [item["element"] for item in base] == [item["element"] for item in with_future]
    assert base == with_future


def test_xg_feature_changes_learned_prediction():
    history = [row(1, element=1, points=2, xg=0.0), row(2, element=1, points=8, xg=2.0),
               row(1, element=2, points=5, xg=1.0), row(2, element=2, points=2, xg=0.0)]
    low = predict_gameweek(history, 3)
    high_history = [dict(item, expected_goals=8.0) if item["element"] == 1 and item["gw"] == 2 else item for item in history]
    high = predict_gameweek(high_history, 3)
    low_player = next(item for item in low if item["element"] == 1)
    high_player = next(item for item in high if item["element"] == 1)
    assert high_player["xg_feature"] > low_player["xg_feature"]
    assert high_player["predicted"] != low_player["predicted"]


def test_duplicate_gameweek_rows_fail_closed():
    try:
        predict_gameweek([row(1), row(1)], 2)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate rows must fail")


def test_target_outcome_is_not_used_to_select_universe():
    history = [row(1, minutes=90), row(2, minutes=0), row(3, minutes=0, points=0)]
    predictions = predict_gameweek(history, 3)
    assert {item["element"] for item in predictions} == {1}


def test_target_outcome_cannot_change_prospective_cohort():
    history = [row(1, minutes=90, points=0), row(2, minutes=0, points=0)]
    changed = [dict(item, total_points=99) if item["gw"] == 2 else item for item in history]
    assert (_prospective_baselines(history, 3, 1, "DEF") is not None) == (_prospective_baselines(changed, 3, 1, "DEF") is not None)


def test_promotion_gate_rejects_inferior_candidate():
    assert _promotion_gate(2.0, [2.1, 2.2]) is True
    assert _promotion_gate(2.3, [2.1, 2.2]) is False
    assert _promotion_gate(2.0, []) is False
    assert _promotion_gate(2.0, [None]) is False
    assert _promotion_gate(float('nan'), [2.1]) is False


def test_new_player_without_prior_metadata_is_not_faked_into_gw1():
    assert predict_gameweek([row(2, element=10)], 2) == []


def test_xg_and_xa_are_exposed_as_features():
    result = predict_player(row(1, xg=1.5, xa=0.7), [row(1, xg=1.5, xa=0.7)], 2)
    assert result["xg_feature"] == 1.5
    assert result["xa_feature"] == 0.7


def test_history_order_cannot_change_predictions():
    history = [row(gw, element=pid, points=pid + gw, team=1)
               for gw in range(1, 4) for pid in range(1, 5)]
    assert predict_gameweek(history, 4) == predict_gameweek(list(reversed(history)), 4)


def test_team_aggregation_weights_gameweeks_not_player_row_order():
    from competitive_v44_candidate import _aggregate
    history = [row(1, element=1, points=1), row(1, element=2, points=9)]
    assert _aggregate(history)["points"] == 5.0
    assert _aggregate(history) == _aggregate(list(reversed(history)))
