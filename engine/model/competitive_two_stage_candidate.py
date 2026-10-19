"""Leakage-safe two-stage offline competitor for the FPL research tournament.

The model estimates minutes state probabilities first, then conditional points
for each state. Every aggregate is built strictly from rows before the target
GW; player and positional shrinkage are fixed before evaluation.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
POSITION_NAMES = ("GKP", "DEF", "MID", "FWD")
STATES = ("dnp", "cameo", "starter")
VERSION = "competitive-two-stage-bayes-v1"


@dataclass(frozen=True)
class Config:
    name: str
    decay: float
    state_prior_weight: float
    conditional_prior_weight: float
    ensemble_weight: float = 0.0


# Prespecified before running the tournament; no scored GW is used for tuning.
CONFIGS = (
    Config("two_stage_bayes", decay=0.72, state_prior_weight=4.0, conditional_prior_weight=6.0),
    Config("two_stage_stable", decay=0.85, state_prior_weight=6.0, conditional_prior_weight=10.0),
    Config("ridge_two_stage_ensemble", decay=0.72, state_prior_weight=4.0, conditional_prior_weight=6.0, ensemble_weight=0.50),
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _pos(value: Any) -> str:
    return POSITIONS.get(int(_f(value, 3)), "MID")


def _aggregate(rows: list[dict[str, Any]], decay: float) -> dict[str, float]:
    if not rows:
        return {"points": 0.0, "minutes": 0.0}
    latest = max(int(_f(row.get("gw"))) for row in rows)
    weights = [decay ** (latest - int(_f(row.get("gw")))) for row in rows]
    total = sum(weights)
    return {
        "points": sum(_f(row.get("total_points")) * weight for row, weight in zip(rows, weights)) / total,
        "minutes": sum(_f(row.get("minutes")) * weight for row, weight in zip(rows, weights)) / total,
    }


def _state(row: dict[str, Any]) -> str:
    minutes = _f(row.get("minutes"))
    return "dnp" if minutes <= 0 else "cameo" if minutes < 60 else "starter"


def _prior(position: str) -> tuple[float, float, float]:
    return {"GKP": (0.08, 0.08, 0.84), "DEF": (0.20, 0.12, 0.68), "MID": (0.18, 0.14, 0.68), "FWD": (0.18, 0.14, 0.68)}[position]


def _state_probabilities(rows: list[dict[str, Any]], position: str, prior_weight: float, decay: float) -> dict[str, float]:
    prior = _prior(position)
    counts = {state: 0.0 for state in STATES}
    for row in rows:
        counts[_state(row)] += 1.0
    total = sum(counts.values())
    if not total:
        return dict(zip(STATES, prior))
    observed = {state: counts[state] / total for state in STATES}
    weight = total / (total + prior_weight)
    # Decay is intentionally used only for conditional scoring; state evidence
    # is observation-count based so one player's row order cannot alter it.
    del decay
    return {state: (1.0 - weight) * prior[index] + weight * observed[state] for index, state in enumerate(STATES)}


def _conditional_means(all_rows: list[dict[str, Any]], position: str, decay: float, prior_weight: float) -> dict[str, float]:
    position_rows = [row for row in all_rows if _pos(row.get("position")) == position]
    result: dict[str, float] = {}
    for state in STATES:
        population = [row for row in position_rows if _state(row) == state]
        prior_mean = 0.0 if state == "dnp" else (1.0 if state == "cameo" else {"GKP": 3.0, "DEF": 2.5, "MID": 3.5, "FWD": 3.5}[position])
        if not population:
            result[state] = prior_mean
            continue
        weighted = _aggregate(population, decay)["points"]
        result[state] = (len(population) * weighted + prior_weight * prior_mean) / (len(population) + prior_weight)
    return result


def _check(history: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in history]
    seen: set[tuple[int, int]] = set()
    for row in rows:
        key = (int(_f(row.get("gw"))), int(_f(row.get("element"))))
        if key in seen:
            raise ValueError(f"duplicate gameweek/player row: {key}")
        seen.add(key)
    return rows


def predict_gameweek(history: list[dict[str, Any]], target_gw: int, config: Config, ridge_predictions: dict[int, float] | None = None) -> list[dict[str, Any]]:
    checked = _check(history)
    prior = [row for row in checked if int(_f(row.get("gw"))) < target_gw]
    by_player: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in prior:
        by_player[int(_f(row.get("element")))].append(row)
    output = []
    for element in sorted(by_player):
        rows = sorted(by_player[element], key=lambda row: int(_f(row.get("gw"))))
        position = _pos(rows[-1].get("position"))
        probs = _state_probabilities(rows, position, config.state_prior_weight, config.decay)
        means = _conditional_means(prior, position, config.decay, config.conditional_prior_weight)
        own_means = _conditional_means(rows, position, config.decay, config.conditional_prior_weight)
        predicted = sum(probs[state] * own_means[state] for state in STATES)
        if config.ensemble_weight and ridge_predictions is not None:
            predicted = (1.0 - config.ensemble_weight) * predicted + config.ensemble_weight * ridge_predictions.get(element, 0.0)
        output.append({"element": element, "position": position, "predicted": round(max(0.0, predicted), 6), "p_dnp": round(probs["dnp"], 6), "p_cameo": round(probs["cameo"], 6), "p_starter": round(probs["starter"], 6), "training_rows": len(rows), "conditional_position_prior": means, "model_version": VERSION + ":" + config.name})
    return output
