"""Calibration and promotion metrics for Competitive V4.2 shadow forecasts."""
from __future__ import annotations

import csv
import math
import os


def load_rows(path, limit=5000):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))[-limit:]
    output = []
    for row in rows:
        try:
            normalized = dict(row)
            for key in ("predicted", "actual", "minutes", "p_dnp", "p_1_59", "p_60_plus"):
                normalized[key] = float(row.get(key) or 0.0)
            output.append(normalized)
        except (TypeError, ValueError):
            continue
    return output


def _quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    low, high = int(math.floor(index)), int(math.ceil(index))
    if low == high:
        return ordered[low]
    return ordered[low] * (high - index) + ordered[high] * (index - low)


def _metrics(rows):
    if not rows:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    errors = [r["predicted"] - r["actual"] for r in rows]
    actual_states = []
    for row in rows:
        minutes = row["minutes"]
        actual_states.append((1.0 if minutes <= 0 else 0.0,
                              1.0 if 0 < minutes < 60 else 0.0,
                              1.0 if minutes >= 60 else 0.0))
    brier = sum(
        (r["p_dnp"] - state[0]) ** 2
        + (r["p_1_59"] - state[1]) ** 2
        + (r["p_60_plus"] - state[2]) ** 2
        for r, state in zip(rows, actual_states)
    ) / (3.0 * len(rows))
    return {
        "n": len(rows),
        "mae": round(sum(abs(e) for e in errors) / len(errors), 4),
        "rmse": round(math.sqrt(sum(e * e for e in errors) / len(errors)), 4),
        "bias": round(sum(errors) / len(errors), 4),
        "brier": round(brier, 5),
        # Actual - predicted offsets create an empirical 80% interval.
        "residual_q10": round(_quantile([-e for e in errors], 0.10), 4),
        "residual_q50": round(_quantile([-e for e in errors], 0.50), 4),
        "residual_q90": round(_quantile([-e for e in errors], 0.90), 4),
    }


def calibration_summary(path, limit=5000):
    rows = load_rows(path, limit)
    result = _metrics(rows)
    result["by_position"] = {}
    for position in sorted({str(r.get("pos") or "?") for r in rows}):
        result["by_position"][position] = _metrics(
            [r for r in rows if str(r.get("pos") or "?") == position]
        )
    return result


def calibrate(mean, variance, summary, position):
    """Apply only well-supported rolling calibration; otherwise stay neutral."""
    group = (summary.get("by_position") or {}).get(position) or {}
    evidence = group if int(group.get("n") or 0) >= 80 else summary
    if int(evidence.get("n") or 0) < 200:
        sd = math.sqrt(max(0.25, variance))
        return mean, variance, max(0.0, mean - 1.28 * sd), mean, mean + 1.28 * sd, 0
    rmse = float(evidence.get("rmse") or math.sqrt(max(0.25, variance)))
    scale = max(0.75, min(1.75, rmse / max(0.5, math.sqrt(max(0.25, variance)))))
    calibrated_variance = variance * scale * scale
    q10 = float(evidence.get("residual_q10") or -1.28 * rmse)
    q50 = float(evidence.get("residual_q50") or 0.0)
    q90 = float(evidence.get("residual_q90") or 1.28 * rmse)
    return (mean, calibrated_variance, max(0.0, mean + q10),
            max(0.0, mean + q50), max(0.0, mean + q90), int(evidence["n"]))
