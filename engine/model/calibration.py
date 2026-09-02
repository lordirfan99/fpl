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


def uncertainty_scale(summary, min_rows=100):
    """Translate rolling residual error into an uncertainty scaling factor."""
    if (not summary or int(summary.get("n") or 0) < min_rows
            or summary.get("rmse") is None):
        return 1.0
    return max(0.75, min(1.75, float(summary["rmse"]) / 3.0))
