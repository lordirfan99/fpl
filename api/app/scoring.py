"""2026/27 FPL scoring primitives used by the isolated V5 lab."""

from __future__ import annotations

import math


GOAL_POINTS = {"GKP": 10, "DEF": 6, "MID": 5, "FWD": 4}
CLEAN_SHEET_POINTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def poisson_at_least(rate: float, threshold: int) -> float:
    """Probability of a Poisson variable meeting an FPL threshold."""
    if threshold <= 0:
        return 1.0
    rate = max(0.0, rate)
    cumulative = sum(math.exp(-rate) * rate ** value / math.factorial(value) for value in range(threshold))
    return clamp(1.0 - cumulative)


def appearance_points(expected_minutes: float, p_60_plus: float) -> float:
    """One point for an appearance, plus one more for 60+ minutes."""
    appearance_probability = clamp(expected_minutes / 15.0)
    return appearance_probability + clamp(p_60_plus)


def defensive_contribution_points(position: str, expected_actions: float) -> float:
    threshold = 10 if position == "DEF" else 12
    if position not in {"DEF", "MID", "FWD"}:
        return 0.0
    return 2.0 * poisson_at_least(expected_actions, threshold)


def goalkeeper_save_points(expected_saves: float) -> float:
    """Expected whole save-bucket points: one per each completed three saves."""
    rate = max(0.0, expected_saves)
    # The tail is negligible after this bound; retain enough buckets for high-save outliers.
    return sum(poisson_at_least(rate, 3 * bucket) for bucket in range(1, 11))


def conceded_points(position: str, expected_goals_conceded: float) -> float:
    if position not in {"GKP", "DEF"}:
        return 0.0
    return -sum(poisson_at_least(max(0.0, expected_goals_conceded), 2 * bucket) for bucket in range(1, 8))
