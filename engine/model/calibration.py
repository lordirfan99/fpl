"""Lightweight rolling calibration utilities for the live xPts model."""
import csv
import math
import os


def mae(rows):
    vals = [abs(float(r["predicted"]) - float(r["actual"])) for r in rows]
    return sum(vals) / len(vals) if vals else 0.0


def rmse(rows):
    vals = [(float(r["predicted"]) - float(r["actual"])) ** 2 for r in rows]
    return math.sqrt(sum(vals) / len(vals)) if vals else 0.0


def bias(rows):
    vals = [float(r["predicted"]) - float(r["actual"]) for r in rows]
    return sum(vals) / len(vals) if vals else 0.0


def load_residuals(path, limit=1000):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    normalized = []
    for r in rows[-limit:]:
        pred = r.get("predicted") or r.get("xpts") or r.get("prediction")
        actual = r.get("actual") or r.get("points")
        if pred is None or actual is None:
            continue
        row = dict(r)
        row.update({"predicted": pred, "actual": actual})
        normalized.append(row)
    return normalized


def calibration_summary(path, limit=1000):
    rows = load_residuals(path, limit)
    if not rows:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    positions = {}
    for position in sorted({str(row.get("pos") or "?") for row in rows}):
        subset = [row for row in rows if str(row.get("pos") or "?") == position]
        positions[position] = {"n": len(subset), "mae": round(mae(subset), 3),
                               "rmse": round(rmse(subset), 3), "bias": round(bias(subset), 3)}
    minute_buckets = {}
    for label, low, high in (("0-59", 0, 60), ("60-74", 60, 75), ("75+", 75, 10000)):
        subset = [row for row in rows if low <= float(row.get("minutes") or 0) < high]
        if subset:
            minute_buckets[label] = {"n": len(subset), "mae": round(mae(subset), 3)}
    interval_rows = [row for row in rows if row.get("floor") not in (None, "")
                     and row.get("upside") not in (None, "")]
    coverage = None
    if interval_rows:
        covered = sum(float(row["floor"]) <= float(row["actual"]) <= float(row["upside"])
                      for row in interval_rows)
        coverage = round(covered / len(interval_rows), 3)
    gameweeks = sorted({int(row["gw"]) for row in rows if str(row.get("gw") or "").isdigit()})
    return {"n": len(rows), "mae": round(mae(rows), 3),
            "rmse": round(rmse(rows), 3), "bias": round(bias(rows), 3),
            "gw_range": [gameweeks[0], gameweeks[-1]] if gameweeks else None,
            "by_position": positions, "by_minutes": minute_buckets,
            "interval_coverage": coverage, "interval_n": len(interval_rows)}


def uncertainty_scale(summary, min_rows=100, target_coverage=0.90):
    """Variance scaling factor from rolling residuals.

    Prefer empirical interval coverage: if the floor..upside band has been
    containing the actual less often than ``target_coverage``, widen it (and
    vice-versa). Fall back to the old rmse/3 heuristic when coverage evidence
    is thin.
    """
    if not summary or int(summary.get("n") or 0) < min_rows:
        return 1.0
    coverage = summary.get("interval_coverage")
    if coverage is not None and int(summary.get("interval_n") or 0) >= min_rows:
        ratio = float(target_coverage) / max(0.30, float(coverage))
        return max(0.75, min(2.5, ratio ** 0.6))
    if summary.get("rmse") is None:
        return 1.0
    return max(0.75, min(1.75, float(summary["rmse"]) / 3.0))


def bias_adjustment(summary, position, *, min_n=60, full_trust_n=250, cap=1.5):
    """Additive xPts correction from rolling residuals (``bias`` = mean of
    predicted - actual, so the correction subtracts it).

    Returns 0.0 until a position has ``min_n`` residuals; shrinks toward 0 by
    sample size up to ``full_trust_n``; hard-capped at +/- ``cap`` so a noisy
    early estimate can never dominate a projection. Falls back to the pooled
    bias when the per-position sample is thin.
    """
    if not summary:
        return 0.0
    row = (summary.get("by_position") or {}).get(str(position)) or {}
    count = int(row.get("n") or 0)
    value = row.get("bias")
    if count < min_n or value is None:
        count = int(summary.get("n") or 0)
        value = summary.get("bias")
        if count < min_n or value is None:
            return 0.0
    shrink = min(1.0, count / float(full_trust_n))
    return round(max(-cap, min(cap, -float(value) * shrink)), 3)
