from __future__ import annotations

from app.projections import project_player


TEAMS = {1: "A"}
PLAYER = {
    "id": 1, "web_name": "Example", "team": 1, "element_type": 2, "status": "a",
    "minutes": 900, "starts": 10, "appearances": 10,
    "expected_goals_per_90": "0.1", "expected_assists_per_90": "0.1",
    "expected_goals_conceded_per_90": "1.2",
}


def home(fdr: int) -> dict:
    return {"team_h": 1, "team_a": 2, "team_h_difficulty": fdr, "team_a_difficulty": 3}


def test_blank_gameweek_is_explicit_and_scores_zero() -> None:
    row = project_player(PLAYER, TEAMS, [])
    assert row.xpts_mean == 0
    assert row.p10 == row.p50 == row.p90 == 0
    assert row.p_return == 0
    assert row.quality_issues == ("blank_gameweek:no_fixture",)


def test_double_gameweek_aggregates_both_fixtures() -> None:
    single = project_player(PLAYER, TEAMS, [home(3)])
    double = project_player(PLAYER, TEAMS, [home(3), {
        "team_h": 3, "team_a": 1, "team_h_difficulty": 3, "team_a_difficulty": 2,
    }])
    assert double.xpts_mean > single.xpts_mean
    assert double.components["appearance"] == 2 * single.components["appearance"]


def test_clean_sheet_component_is_monotonic_with_fixture_ease() -> None:
    easy = project_player(PLAYER, TEAMS, [home(1)])
    hard = project_player(PLAYER, TEAMS, [home(5)])
    assert easy.components["clean_sheet"] > hard.components["clean_sheet"]
