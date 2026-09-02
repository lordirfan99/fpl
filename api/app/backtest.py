"""Leakage-aware scoring primitives for frozen gameweek predictions.

The module scores only rows explicitly paired with outcomes.  It never fills
missing predictions from a newer bootstrap snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable


@dataclass(frozen=True)
class PairedRow:
    gameweek: int
    element: int
    position: str
    predicted: float
    actual: float
    probabilities: dict[str, float]
    events: dict[str, bool]


def _ranks(values: list[float]) -> list[float]:
    """Return average one-based ranks, preserving ties."""
    ordered = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[cursor]]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for index in ordered[cursor:end]:
            ranks[index] = rank
        cursor = end
    return ranks


def spearman(predicted: list[float], actual: list[float]) -> float | None:
    if len(predicted) < 2 or len(predicted) != len(actual):
        return None
    left, right = _ranks(predicted), _ranks(actual)
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = math.sqrt(
        sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right)
    )
    return None if denominator == 0 else numerator / denominator


def calibration(probabilities: list[float], outcomes: list[bool], bins: int = 10) -> dict[str, Any]:
    if not probabilities or len(probabilities) != len(outcomes):
        return {"n": 0, "brier": None, "bins": []}
    bounded = [max(0.0, min(1.0, value)) for value in probabilities]
    rows = []
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [position for position, value in enumerate(bounded)
                   if low <= value <= high and (index == bins - 1 or value < high)]
        if members:
            rows.append({
                "low": low, "high": high, "n": len(members),
                "mean_probability": round(sum(bounded[i] for i in members) / len(members), 4),
                "event_rate": round(sum(bool(outcomes[i]) for i in members) / len(members), 4),
            })
    brier = sum((value - float(outcome)) ** 2 for value, outcome in zip(bounded, outcomes)) / len(bounded)
    return {"n": len(bounded), "brier": round(brier, 6), "bins": rows}


def score(rows: Iterable[PairedRow], top_k: int = 5) -> dict[str, Any]:
    paired = list(rows)
    if not paired:
        return {
            "status": "insufficient_evidence", "n": 0, "mae": None, "rmse": None,
            "bias": None, "spearman": None, "top_k_actual_mean": None,
            "by_gameweek": {}, "by_position": {}, "calibration": {},
        }
    errors = [row.predicted - row.actual for row in paired]
    ranked = sorted(paired, key=lambda row: row.predicted, reverse=True)[:max(1, min(top_k, len(paired)))]

    def subgroup(group: list[PairedRow]) -> dict[str, Any]:
        group_errors = [row.predicted - row.actual for row in group]
        correlation = spearman([row.predicted for row in group], [row.actual for row in group])
        return {
            "n": len(group),
            "mae": round(sum(abs(value) for value in group_errors) / len(group), 6),
            "rmse": round(math.sqrt(sum(value * value for value in group_errors) / len(group)), 6),
            "bias": round(sum(group_errors) / len(group), 6),
            "spearman": None if correlation is None else round(correlation, 6),
        }

    result = {"status": "measured", **subgroup(paired)}
    result["top_k_actual_mean"] = round(sum(row.actual for row in ranked) / len(ranked), 6)
    result["by_gameweek"] = {
        str(value): subgroup([row for row in paired if row.gameweek == value])
        for value in sorted({row.gameweek for row in paired})
    }
    result["by_position"] = {
        value: subgroup([row for row in paired if row.position == value])
        for value in ("GKP", "DEF", "MID", "FWD")
        if any(row.position == value for row in paired)
    }
    probability_names = sorted({name for row in paired for name in row.probabilities})
    result["calibration"] = {
        name: calibration(
            [row.probabilities[name] for row in paired if name in row.probabilities and name in row.events],
            [row.events[name] for row in paired if name in row.probabilities and name in row.events],
        )
        for name in probability_names
    }
    return result


def pair_model_rows(
    *, gameweek: int, predictions: list[dict[str, Any]], actual_rows: list[dict[str, Any]],
    prediction_field: str,
) -> list[PairedRow]:
    """Pair by official element ID; silently missing rows are never imputed."""
    actual = {int(row["element"]): row for row in actual_rows if row.get("element")}
    paired = []
    for prediction in predictions:
        element = int(prediction.get("element") or prediction.get("id") or 0)
        outcome = actual.get(element)
        if not outcome or prediction.get(prediction_field) is None:
            continue
        minutes = float(outcome.get("minutes") or 0)
        points = float(outcome.get("points") or outcome.get("total_points") or 0)
        expected_minutes = prediction.get("expected_minutes") or {}
        probabilities = {
            key: float(value) for key, value in {
                "start": expected_minutes.get("p_start"),
                "60_plus": expected_minutes.get("p_60_plus"),
                "10_plus": prediction.get("p_10_plus"),
            }.items() if value is not None
        }
        events = {"start": minutes > 0, "60_plus": minutes >= 60, "10_plus": points >= 10}
        paired.append(PairedRow(
            gameweek=gameweek, element=element, position=str(outcome.get("position") or "UNKNOWN"),
            predicted=float(prediction[prediction_field]), actual=points,
            probabilities=probabilities, events=events,
        ))
    return paired

