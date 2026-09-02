from app.scoring import (
    appearance_points,
    defensive_contribution_points,
    goalkeeper_save_points,
)


def test_2026_appearance_scoring_uses_one_and_two_point_bands() -> None:
    assert appearance_points(0, 0) == 0
    assert appearance_points(15, 0) == 1
    assert appearance_points(90, 1) == 2


def test_defensive_contributions_use_threshold_probability_not_linear_scaling() -> None:
    value = defensive_contribution_points("DEF", 9.5)
    assert 0 < value < 2
    assert defensive_contribution_points("MID", 0) == 0


def test_goalkeeper_saves_are_whole_three_save_buckets_in_expectation() -> None:
    assert goalkeeper_save_points(0) == 0
    assert goalkeeper_save_points(6) > goalkeeper_save_points(3)
