"""Leakage-safe, lagged empirical-Bayes FPL point candidate.

This is offline/shadow code. ``predict_player`` only consumes rows with
``gw < target_gw``; current bootstrap forecasts and official ``ep_next`` are
intentionally absent from the interface.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable

VERSION = "competitive-v4.3-lagged-shrink"
POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
GOAL_POINTS = {"GKP": 10.0, "DEF": 6.0, "MID": 5.0, "FWD": 4.0}
CS_POINTS = {"GKP": 4.0, "DEF": 4.0, "MID": 1.0, "FWD": 0.0}
# Per-90 priors are deliberately conservative and only used as shrinkage anchors.
PRIORS = {
    "GKP": {"goals_scored": 0.00, "assists": 0.01, "expected_goals": 0.00, "expected_assists": 0.01, "saves": 3.0, "defensive_contribution": 0.0, "yellow_cards": 0.05, "red_cards": 0.005, "clean_sheets": 0.18},
    "DEF": {"goals_scored": 0.06, "assists": 0.08, "expected_goals": 0.06, "expected_assists": 0.07, "saves": 0.0, "defensive_contribution": 7.0, "yellow_cards": 0.14, "red_cards": 0.01, "clean_sheets": 0.30},
    "MID": {"goals_scored": 0.22, "assists": 0.20, "expected_goals": 0.20, "expected_assists": 0.18, "saves": 0.0, "defensive_contribution": 5.0, "yellow_cards": 0.12, "red_cards": 0.008, "clean_sheets": 0.22},
    "FWD": {"goals_scored": 0.34, "assists": 0.14, "expected_goals": 0.32, "expected_assists": 0.13, "saves": 0.0, "defensive_contribution": 2.0, "yellow_cards": 0.10, "red_cards": 0.008, "clean_sheets": 0.05},
}
RATE_FIELDS = tuple(PRIORS["MID"])
SHRINK_MINUTES = 270.0


def _f(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _state_probs(rows: list[dict[str, Any]], position: str) -> tuple[float, float, float]:
    """Shrink minutes states to a position prior; states are DNP, cameo, 60+."""
    prior = {"GKP": (0.08, 0.08, 0.84), "DEF": (0.20, 0.12, 0.68), "MID": (0.18, 0.14, 0.68), "FWD": (0.18, 0.14, 0.68)}[position]
    exposure = sum(max(0.0, _f(row.get("minutes"))) for row in rows)
    counts = [0.0, 0.0, 0.0]
    for row in rows:
        minutes = _f(row.get("minutes"))
        counts[0 if minutes <= 0 else 1 if minutes < 60 else 2] += 1.0
    weight = min(1.0, exposure / 900.0)
    total = sum(counts)
    observed = tuple(value / total for value in counts) if total else prior
    return (
        (1.0 - weight) * prior[0] + weight * observed[0],
        (1.0 - weight) * prior[1] + weight * observed[1],
        (1.0 - weight) * prior[2] + weight * observed[2],
    )


def _rates(rows: list[dict[str, Any]], position: str) -> dict[str, float]:
    prior = PRIORS[position]
    minutes = sum(max(0.0, _f(row.get("minutes"))) for row in rows)
    # Recent observations matter, but their effective exposure is still shrunk.
    weighted_minutes = sum(max(0.0, _f(row.get("minutes"))) * (0.70 ** max(0, len(rows) - 1 - i)) for i, row in enumerate(rows))
    effective = weighted_minutes + SHRINK_MINUTES
    output = {}
    for field in RATE_FIELDS:
        if field == "clean_sheets":
            observed = sum(_f(row.get(field)) for row in rows) / max(1.0, len(rows))
            output[field] = (observed * min(len(rows), 4) + prior[field] * 4.0) / (min(len(rows), 4) + 4.0)
            continue
        numerator = sum(_f(row.get(field)) * (0.70 ** max(0, len(rows) - 1 - i)) for i, row in enumerate(rows))
        output[field] = (90.0 * numerator / max(1.0, weighted_minutes) * weighted_minutes / effective + prior[field] * SHRINK_MINUTES / effective)
    output["sample_minutes"] = minutes
    return output


def _threshold_probability(mean_rate: float, rows: list[dict[str, Any]], position: str) -> float:
    """Probability of clearing the defensive-contribution threshold, not mean>=threshold."""
    threshold = 10.0 if position == "DEF" else 12.0
    observed = [_f(row.get("defensive_contribution")) * 90.0 / max(1.0, _f(row.get("minutes"))) for row in rows if _f(row.get("minutes")) > 0]
    scale = max(2.5, math.sqrt(sum((x - mean_rate) ** 2 for x in observed) / max(1, len(observed)))) if observed else 4.0
    # Logistic CDF is a bounded approximation to the empirical threshold event.
    return 1.0 / (1.0 + math.exp(-(mean_rate - threshold) / scale))


def predict_player(element: dict[str, Any], prior_rows: Iterable[dict[str, Any]], target_gw: int) -> dict[str, Any]:
    """Predict one player's target GW from strictly prior rows."""
    rows = sorted((dict(row) for row in prior_rows if int(_f(row.get("gw"))) < target_gw), key=lambda row: int(_f(row.get("gw"))))
    position = POSITIONS.get(int(_f(element.get("position") or element.get("element_type"), 3)), "MID")
    rates = _rates(rows, position)
    p_dnp, p_cameo, p_60 = _state_probs(rows, position)
    share = (p_cameo / 2.0 + p_60)  # expected appearance points / 2, used below
    dc_probability = _threshold_probability(rates["defensive_contribution"], rows, position) if position in ("DEF", "MID") else 0.0
    components = {
        "appearance": p_cameo + 2.0 * p_60,
        "goals": rates["goals_scored"] * share * GOAL_POINTS[position],
        "assists": rates["assists"] * share * 3.0,
        "clean_sheet": rates["clean_sheets"] * p_60 * CS_POINTS[position],
        "saves": rates["saves"] * share / 3.0 if position == "GKP" else 0.0,
        "defensive_threshold": 2.0 * dc_probability * p_60 if position in ("DEF", "MID") else 0.0,
        "discipline": -(rates["yellow_cards"] + 3.0 * rates["red_cards"]) * share,
    }
    mean = max(0.0, sum(components.values()))
    return {"model_version": VERSION, "element": int(element["element"]), "position": position,
            "gw": target_gw, "predicted": round(mean, 6), "p_dnp": round(p_dnp, 6),
            "p_1_59": round(p_cameo, 6), "p_60_plus": round(p_60, 6),
            "p_defensive_threshold": round(dc_probability, 6), "expected_minutes": round(59.0 * p_cameo + 75.0 * p_60, 3),
            "sample_minutes": round(rates["sample_minutes"], 3), "training_rows": len(rows),
            "fallback": not bool(rows), "components": {key: round(value, 6) for key, value in components.items()}}


def predict_gameweek(history: list[dict[str, Any]], target_gw: int) -> list[dict[str, Any]]:
    prior_by_element: dict[int, list[dict[str, Any]]] = defaultdict(list)
    elements: dict[int, dict[str, Any]] = {}
    for row in history:
        element = int(row["element"])
        elements[element] = {"element": element, "position": row.get("position")}
        if int(row["gw"]) < target_gw:
            prior_by_element[element].append(row)
    return [predict_player(elements[element], prior_by_element[element], target_gw) for element in sorted(elements)]
