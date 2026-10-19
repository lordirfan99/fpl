from competitive_v43_candidate import predict_player, predict_gameweek


def row(gw, minutes=90, points=6, dc=0, goals=0, assists=0, xg=0, xa=0, cs=0):
    return {"gw": gw, "element": 1, "position": 2, "minutes": minutes, "total_points": points,
            "defensive_contribution": dc, "goals_scored": goals, "assists": assists,
            "expected_goals": xg, "expected_assists": xa, "saves": 0, "yellow_cards": 0,
            "red_cards": 0, "clean_sheets": cs}


def test_prediction_excludes_future_rows_and_reports_fallback():
    element = {"element": 1, "position": 2}
    future = row(2, minutes=90, points=20, goals=3)
    prediction = predict_player(element, [future], 2)
    assert prediction["training_rows"] == 0
    assert prediction["fallback"] is True


def test_lagged_history_is_used_for_target_only():
    element = {"element": 1, "position": 2}
    prediction = predict_player(element, [row(1, dc=15, points=10)], 2)
    assert prediction["training_rows"] == 1
    assert prediction["sample_minutes"] == 90
    assert 0 < prediction["p_defensive_threshold"] < 1


def test_defensive_threshold_is_probability_not_boolean_mean_check():
    element = {"element": 1, "position": 2}
    below = predict_player(element, [row(1, dc=9)], 2)
    above = predict_player(element, [row(1, dc=11)], 2)
    assert 0 < below["p_defensive_threshold"] < 1
    assert above["p_defensive_threshold"] > below["p_defensive_threshold"]
    assert below["components"]["defensive_threshold"] != 2.0


def test_gameweek_has_one_prediction_per_known_player():
    history = [row(1), {**row(1), "element": 2, "position": 3}]
    predictions = predict_gameweek(history, 2)
    assert {p["element"] for p in predictions} == {1, 2}
