from __future__ import annotations

from app.backtest import PairedRow, calibration, pair_model_rows, score, spearman


def test_error_ranking_and_position_metrics_are_exact() -> None:
    rows = [
        PairedRow(1, 1, "MID", 5, 7, {"10_plus": .2}, {"10_plus": False}),
        PairedRow(1, 2, "FWD", 8, 10, {"10_plus": .8}, {"10_plus": True}),
        PairedRow(2, 3, "MID", 3, 1, {"10_plus": .1}, {"10_plus": False}),
    ]
    result = score(rows, top_k=2)
    assert result["n"] == 3
    assert result["mae"] == 2
    assert result["rmse"] == 2
    assert result["bias"] == -0.666667
    assert result["top_k_actual_mean"] == 8.5
    assert result["by_gameweek"]["1"]["n"] == 2
    assert result["by_position"]["MID"]["n"] == 2
    assert result["calibration"]["10_plus"]["brier"] == 0.03


def test_rank_and_calibration_edge_cases_do_not_invent_metrics() -> None:
    assert spearman([1], [2]) is None
    assert spearman([1, 1], [2, 3]) is None
    assert calibration([], [])["brier"] is None
    assert score([])["status"] == "insufficient_evidence"


def test_pairing_uses_element_ids_and_never_imputes_missing_players() -> None:
    predictions = [{"element": 1, "xpts_mean": 5, "p_10_plus": .25,
                    "expected_minutes": {"p_start": .8, "p_60_plus": .6}},
                   {"element": 2, "xpts_mean": 9}]
    actual = [{"element": 1, "position": "MID", "points": 10, "minutes": 90}]
    rows = pair_model_rows(gameweek=4, predictions=predictions, actual_rows=actual,
                           prediction_field="xpts_mean")
    assert len(rows) == 1
    assert rows[0].element == 1
    assert rows[0].events == {"start": True, "60_plus": True, "10_plus": True}

