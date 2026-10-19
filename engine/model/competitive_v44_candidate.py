"""Iteration-2 leakage-safe learned FPL point model.

The model is deliberately offline: every feature for gameweek ``t`` is built
from rows with ``gw < t``.  A ridge fit is rebuilt for each target gameweek,
which keeps the walk-forward evaluation chronological and avoids target
outcome leakage.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable

VERSION = "competitive-v4.4-walkforward-ridge"
POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
POSITION_NAMES = ("GKP", "DEF", "MID", "FWD")
FEATURE_NAMES = ("bias", "gkp", "def", "mid", "fwd", "minutes", "starts", "xg", "xa", "points", "bonus", "goals", "assists", "team_points", "team_xg")
RIDGE = 12.0


def _f(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _position(value: Any) -> str:
    return POSITIONS.get(int(_f(value, 3)), "MID")


def _check_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output = [dict(row) for row in rows]
    seen: set[tuple[int, int]] = set()
    for row in output:
        key = (int(_f(row.get("gw"))), int(_f(row.get("element"))))
        if key in seen:
            raise ValueError(f"duplicate gameweek/player row: {key}")
        seen.add(key)
    return output


def _state_probs(rows: list[dict[str, Any]], position: str) -> tuple[float, float, float]:
    """Estimate DNP/cameo/60+ with games, not minutes, as observation weight."""
    priors = {"GKP": (0.08, 0.08, 0.84), "DEF": (0.20, 0.12, 0.68), "MID": (0.18, 0.14, 0.68), "FWD": (0.18, 0.14, 0.68)}
    prior = priors[position]
    counts = [0.0, 0.0, 0.0]
    for row in rows:
        minutes = _f(row.get("minutes"))
        counts[0 if minutes <= 0 else 1 if minutes < 60 else 2] += 1.0
    # Four pseudo-games prevent tiny histories from becoming overconfident,
    # while six DNP observations still correctly move the estimate near DNP.
    weight = len(rows) / (len(rows) + 4.0)
    total = sum(counts)
    observed = tuple(value / total for value in counts) if total else prior
    values = tuple((1.0 - weight) * p + weight * o for p, o in zip(prior, observed))
    return values[0], values[1], values[2]


def _aggregate(rows: list[dict[str, Any]], decay: float = 0.72) -> dict[str, float]:
    if not rows:
        return {name: 0.0 for name in ("minutes", "starts", "xg", "xa", "points", "bonus", "goals", "assists")}
    ordered = sorted(rows, key=lambda row: int(_f(row.get("gw"))))
    latest_gw = max(int(_f(row.get("gw"))) for row in ordered)
    weights = [decay ** (latest_gw - int(_f(row.get("gw")))) for row in ordered]
    total = sum(weights)
    fields = ("minutes", "starts", "expected_goals", "expected_assists", "total_points", "bonus", "goals_scored", "assists")
    result = {}
    for field, name in zip(fields, ("minutes", "starts", "xg", "xa", "points", "bonus", "goals", "assists")):
        result[name] = sum(_f(row.get(field)) * weight for row, weight in zip(ordered, weights)) / total
    return result


def _feature_vector(element: dict[str, Any], prior_rows: list[dict[str, Any]], team_rows: list[dict[str, Any]]) -> list[float]:
    position = _position(element.get("position") or element.get("element_type"))
    recent = _aggregate(prior_rows)
    team = _aggregate(team_rows)
    vector = [1.0] + [1.0 if position == name else 0.0 for name in POSITION_NAMES]
    vector += [recent[name] for name in ("minutes", "starts", "xg", "xa", "points", "bonus", "goals", "assists")]
    vector += [team["points"], team["xg"]]
    return vector


def _ridge_fit(samples: list[tuple[list[float], float]]) -> list[float]:
    size = len(FEATURE_NAMES)
    matrix = [[0.0] * (size + 1) for _ in range(size)]
    for vector, target in samples:
        for i in range(size):
            for j in range(size):
                matrix[i][j] += vector[i] * vector[j]
            matrix[i][-1] += vector[i] * target
    for i in range(1, size):
        matrix[i][i] += RIDGE
    matrix[0][0] += 0.1
    for pivot in range(size):
        row = max(range(pivot, size), key=lambda index: abs(matrix[index][pivot]))
        if abs(matrix[row][pivot]) < 1e-12:
            raise ValueError("singular ridge system")
        matrix[pivot], matrix[row] = matrix[row], matrix[pivot]
        scale = matrix[pivot][pivot]
        matrix[pivot] = [value / scale for value in matrix[pivot]]
        for row in range(size):
            if row == pivot:
                continue
            factor = matrix[row][pivot]
            if factor:
                matrix[row] = [a - factor * b for a, b in zip(matrix[row], matrix[pivot])]
    return [matrix[i][-1] for i in range(size)]


def _contexts(history: list[dict[str, Any]], target_gw: int) -> tuple[dict[int, list[dict[str, Any]]], dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    prior = [row for row in history if int(_f(row.get("gw"))) < target_gw]
    by_element: dict[int, list[dict[str, Any]]] = defaultdict(list)
    elements: dict[int, dict[str, Any]] = {}
    by_team: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(prior, key=lambda item: (int(_f(item.get("gw"))), int(_f(item.get("element"))))):
        element = int(_f(row.get("element")))
        team = int(_f(row.get("team")))
        by_element[element].append(row)
        by_team[team].append(row)
        elements[element] = {"element": element, "position": row.get("position"), "team": team}
    return by_element, elements, by_team


def _fit_for_target(history: list[dict[str, Any]], target_gw: int) -> tuple[list[float], int]:
    by_element, elements, by_team = _contexts(history, target_gw)
    samples = []
    for row in sorted(history, key=lambda item: (int(_f(item.get("gw"))), int(_f(item.get("element"))))):
        gw = int(_f(row.get("gw")))
        if gw >= target_gw:
            continue
        element = int(_f(row.get("element")))
        prior_rows = [item for item in by_element[element] if int(_f(item.get("gw"))) < gw]
        team_rows = [item for item in by_team[int(_f(row.get("team")))] if int(_f(item.get("gw"))) < gw]
        if not prior_rows:
            continue
        metadata = max(prior_rows, key=lambda item: int(_f(item.get("gw"))))
        vector = _feature_vector(metadata, prior_rows, team_rows)
        samples.append((vector, _f(row.get("total_points"))))
    return _ridge_fit(samples), len(samples)


def predict_player(element: dict[str, Any], prior_rows: Iterable[dict[str, Any]], target_gw: int, model: list[float] | None = None, team_rows: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    rows = sorted((dict(row) for row in prior_rows if int(_f(row.get("gw"))) < target_gw), key=lambda row: int(_f(row.get("gw"))))
    position = _position(element.get("position") or element.get("element_type"))
    probs = _state_probs(rows, position)
    vector = _feature_vector(element, rows, [dict(row) for row in team_rows])
    if model is None:
        model = _ridge_fit([(vector, _f(rows[-1].get("total_points")))]) if rows else [0.0] * len(FEATURE_NAMES)
    raw = sum(a * b for a, b in zip(model, vector))
    predicted = max(0.0, raw)
    return {"model_version": VERSION, "element": int(element["element"]), "position": position, "gw": target_gw,
            "predicted": round(predicted, 6), "p_dnp": round(probs[0], 6), "p_1_59": round(probs[1], 6), "p_60_plus": round(probs[2], 6),
            "expected_minutes": round(59.0 * probs[1] + 75.0 * probs[2], 3), "sample_minutes": round(sum(_f(r.get("minutes")) for r in rows), 3),
            "training_rows": len(rows), "fallback": not bool(rows), "feature_vector": {name: round(value, 6) for name, value in zip(FEATURE_NAMES, vector)},
            "xg_feature": round(vector[7], 6), "xa_feature": round(vector[8], 6), "ridge_training_samples": len(rows),
            "components": {"learned_ridge": round(predicted, 6)}}


def predict_gameweek(history: list[dict[str, Any]], target_gw: int) -> list[dict[str, Any]]:
    checked = _check_rows(history)
    by_element, elements, by_team = _contexts(checked, target_gw)
    if not elements:
        return []
    model, training_samples = _fit_for_target(checked, target_gw) if any(int(_f(r.get("gw"))) < target_gw for r in checked) else ([0.0] * len(FEATURE_NAMES), 0)
    output = []
    for element in sorted(elements):
        prediction = predict_player(elements[element], by_element[element], target_gw, model, by_team[elements[element]["team"]])
        prediction["ridge_training_samples"] = training_samples
        output.append(prediction)
    return output
