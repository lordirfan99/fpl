from competitive_two_stage_candidate import CONFIGS, _state_probabilities, predict_gameweek


def row(gw, element=1, minutes=90, points=6, position=2, team=1):
    return {"gw": gw, "element": element, "position": position, "team": team, "minutes": minutes, "total_points": points}


def test_dnp_observations_reduce_starter_probability_and_are_zero_points():
    dnp = _state_probabilities([row(gw, minutes=0, points=0) for gw in range(1, 7)], "DEF", 4.0, 0.72)
    starter = _state_probabilities([row(gw) for gw in range(1, 7)], "DEF", 4.0, 0.72)
    assert dnp["starter"] < starter["starter"]
    assert dnp["dnp"] > 0.5


def test_future_rows_cannot_change_universe_or_prediction():
    prior = [row(1, points=4)]
    base = predict_gameweek(prior, 2, CONFIGS[0])
    future = prior + [row(2, element=99, points=50), row(3, points=50)]
    assert predict_gameweek(future, 2, CONFIGS[0]) == base


def test_duplicate_rows_fail_closed():
    try:
        predict_gameweek([row(1), row(1)], 2, CONFIGS[0])
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate observations must fail")


def test_history_order_does_not_change_prediction():
    history = [row(gw, element=element, points=gw + element) for gw in range(1, 4) for element in range(1, 4)]
    assert predict_gameweek(history, 4, CONFIGS[0]) == predict_gameweek(list(reversed(history)), 4, CONFIGS[0])
