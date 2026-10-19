"""Regression tests for the production learned-v5 seam."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from learned_v5_projection import (
    VERSION,
    LearnedModelUnavailable,
    activation,
    predict_catalog,
    reset_cache,
)
from proposal_binding import canonical_plan_hash
from pre_deadline_run import active_manual_out
from post_gw_review import merge_residual_rows, residual_target
from horizon_milp import _captain_utility


def _row(gw: int, element: int, points: float, minutes: int = 90) -> dict:
    return {
        "gw": gw, "element": element, "team": element % 5 + 1, "position": 3,
        "minutes": minutes, "starts": int(minutes >= 60), "total_points": points,
        "expected_goals": points / 20, "expected_assists": points / 30,
        "goals_scored": 0, "assists": 0, "bonus": 0,
    }


def _snapshot(gw: int, n: int = 10) -> list[dict]:
    return [{
        "id": element, "element_type": 3, "team": element % 5 + 1,
        "ep_next": 4.0 + element / 100, "form": "4.0", "points_per_game": "4.0",
        "value_form": "0.5", "value_season": "0.5", "selected_by_percent": "5",
        "now_cost": 70, "minutes": gw * 60, "starts": gw - 1, "total_points": gw * 4,
        "expected_goals": 0.1, "expected_assists": 0.1,
        "expected_goal_involvements": 0.2, "expected_goals_per_90": 0.1,
        "expected_assists_per_90": 0.1, "expected_goal_involvements_per_90": 0.2,
        "influence": 10, "creativity": 10, "threat": 10, "ict_index": 10,
        "bps": 10, "bonus": 0, "clean_sheets": 0, "goals_scored": 0,
        "assists": 0, "penalties_order": 0,
    } for element in range(1, n + 1)]


def _fixture_rows() -> list[dict]:
    return [{"event": gw, "team_h": 1, "team_a": 2,
             "team_h_difficulty": 2, "team_a_difficulty": 5}
            for gw in (8, 9, 10)] + [{"event": 8, "team_h": 1, "team_a": 3,
                                      "team_h_difficulty": 3, "team_a_difficulty": 4}]


def _fixture_archive(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    root.mkdir()
    for gw in range(2, 8):
        (root / f"predeadline_gw{gw}.json").write_text(json.dumps({"elements": _snapshot(gw)}))


def test_training_uses_only_prior_current_season_rows_and_excludes_gw1(tmp_path):
    _fixture_archive(tmp_path)
    history = [_row(1, element, 99) for element in range(1, 11)]
    history += [_row(gw, element, gw + element / 10) for gw in range(2, 9) for element in range(1, 11)]
    current = _snapshot(8)
    reset_cache()
    output = predict_catalog(history, current, [8, 9, 10], _fixture_rows(), tmp_path / "snapshots")
    assert output[1]["model_version"] == VERSION
    assert output[1]["training_gameweeks"] == [2, 3, 4, 5, 6, 7]
    assert output[1]["training_rows"] == 60


def test_target_outcome_mutation_cannot_change_frozen_forecast(tmp_path):
    _fixture_archive(tmp_path)
    history = [_row(gw, element, gw + element / 10) for gw in range(2, 9) for element in range(1, 11)]
    current = _snapshot(8)
    reset_cache()
    first = predict_catalog(history, current, [8, 9, 10], _fixture_rows(), tmp_path / "snapshots")[1]
    history.append(_row(8, 1, 9999))
    reset_cache()
    second = predict_catalog(history, current, [8, 9, 10], _fixture_rows(), tmp_path / "snapshots")[1]
    assert first["xpts_by_gw"] == second["xpts_by_gw"]


def test_future_fixture_adjustment_handles_double_without_future_outcomes(tmp_path):
    _fixture_archive(tmp_path)
    history = [_row(gw, element, 4) for gw in range(2, 8) for element in range(1, 11)]
    result = predict_catalog(history, _snapshot(8), [8, 9, 10], _fixture_rows(), tmp_path / "snapshots")[5]
    assert result["fixture_features"][0]["fixture_count"] == 2
    assert result["fixture_features"][0]["fdr"] == pytest.approx(2.5)


def test_fixture_count_zero_is_zero_forecast(tmp_path):
    _fixture_archive(tmp_path)
    history = [_row(gw, element, 4) for gw in range(2, 8) for element in range(1, 11)]
    result = predict_catalog(history, _snapshot(8), [8, 11], _fixture_rows(), tmp_path / "snapshots")[5]
    assert result["fixture_features"][1]["fixture_count"] == 0
    assert result["xpts_by_gw"][1] == 0


def test_activation_is_explicit(tmp_path):
    assert activation({}, tmp_path / "missing.json")[0] is False
    assert activation({"learned_model": {"enabled": True, "version": VERSION}})[0] is True
    (tmp_path / "registry.json").write_text(json.dumps({"active_projection": VERSION}))
    assert activation({}, tmp_path / "registry.json") == (True, "registry")


def test_missing_training_fails_closed(tmp_path):
    with pytest.raises(LearnedModelUnavailable, match="requires >="):
        predict_catalog([_row(2, 1, 4)], _snapshot(8), [8, 9, 10], [], tmp_path)


def test_canonical_approval_hash_ignores_model_display_noise():
    plan = {"gw": 8, "transfers": [], "target_starters": [{"id": 154}],
            "bench": [], "captain": {"id": 154}, "vice": {"id": 155}, "chip": None,
            "model_version": VERSION}
    original = canonical_plan_hash(plan)
    plan["model_version"] = "other-display-only-value"
    assert canonical_plan_hash(plan) == original


def test_preference_contract_represents_bruno_out_and_palmer_keep():
    prefs = {"keep": [154], "exclude": [], "manual_out": [426], "manual_out_gw": 8}
    assert 154 in set(prefs["keep"])
    assert 426 in set(prefs["manual_out"])
    assert prefs["manual_out_gw"] == 8
    assert active_manual_out(prefs, 8) == {426}
    assert active_manual_out(prefs, 9) == set()


def test_learned_residuals_are_versioned_and_idempotent(tmp_path):
    assert residual_target("learned-v5.0") == ("residuals_learned_v5.csv", True)
    assert residual_target("recency-v1")[0] == "residuals_recency_v1.csv"
    row = {"gw": 8, "element": 426, "predicted": 4.0}
    path = tmp_path / "residuals.csv"
    merge_residual_rows(path, [row], deduplicate=True)
    merge_residual_rows(path, [dict(row, predicted=9.0)], deduplicate=True)
    assert path.read_text().count("426") == 1
    assert "9.0" in path.read_text()


def test_learned_captain_objective_is_mean_only():
    player = {"position": "DEF", "xpts_by_gw": [5.0], "variance_by_gw": [9.0]}
    assert _captain_utility(player, 0, mean_only=True) == 5.0
    assert _captain_utility(player, 0) > 5.0
