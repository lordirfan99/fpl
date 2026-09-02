from app.projections import expected_minutes


def test_unavailable_player_has_zero_minutes() -> None:
    result = expected_minutes({"status": "u", "minutes": 900, "starts": 10})
    assert result.expected_minutes == 0
    assert result.p_start == 0


def test_minutes_probabilities_are_bounded() -> None:
    result = expected_minutes({"status": "a", "minutes": 720, "starts": 8, "appearances": 9})
    assert 0 <= result.p_start <= 1
    assert 0 <= result.p_bench_appearance <= 1
    assert 0 <= result.p_60_plus <= 1
    assert 0 <= result.expected_minutes <= 90
