"""Lagged attacking rates: recent evidence without chasing last week's points.

Keep 30% of the season signal and blend 70% of the last four GWs, with a
three-GW half-life within that window.
Actual FPL points are diagnostics only; they never change the mean forecast.
"""
from __future__ import annotations

import math


VERSION = "recency-v1"
ATTACK_FIELDS = ("goals_scored", "assists", "expected_goals", "expected_assists")


def _number(value):
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def attacking_form(history, target_gw):
    """Return unshrunk per-90 rates and evidence from completed, prior GWs.

    The caller supplies data-checked event rows, not fixture-level rows. Require
    the last three GWs and 180 minutes so gaps, missing xG and sparse samples
    cannot quietly become a sell signal. Future rows are always excluded.
    """
    rows = {}
    for row in history or []:
        gw = _number(row.get("gw"))
        if gw is not None and gw.is_integer() and 0 < gw < target_gw:
            rows[int(gw)] = row
    ordered = [rows[gw] for gw in sorted(rows)]
    evidence = {"version": VERSION, "status": "insufficient_history",
                "sample_gws": len(ordered), "single_haul_dependency": False}
    if not set(range(max(1, target_gw - 4), target_gw)).issubset(rows) or len(rows) < 3:
        return None, evidence
    for row in ordered:
        if any(_number(row.get(field)) is None for field in ("minutes", *ATTACK_FIELDS)):
            evidence["status"] = "incomplete_attacking_history"
            return None, evidence
    minutes = sum(float(row["minutes"]) for row in ordered)
    recent = [row for row in ordered if int(row["gw"]) >= target_gw - 3]
    recent_minutes = sum(float(row["minutes"]) for row in recent)
    if minutes < 180 or recent_minutes < 180:
        return None, evidence
    weights = [0.5 ** ((target_gw - 1 - int(row["gw"])) / 3.0)
               if int(row["gw"]) >= target_gw - 4 else 0.0 for row in ordered]
    weighted_minutes = sum(weight * float(row["minutes"])
                           for weight, row in zip(weights, ordered))
    rates, season = {}, {}
    for field in ATTACK_FIELDS:
        season[field] = 90.0 * sum(float(row[field]) for row in ordered) / minutes
        weighted_rate = 90.0 * sum(weight * float(row[field])
                                   for weight, row in zip(weights, ordered)) / weighted_minutes
        rates[field] = 0.30 * season[field] + 0.70 * weighted_rate
    season_xgi = season["expected_goals"] + season["expected_assists"]
    recent_xgi = 90.0 * sum(float(row["expected_goals"]) + float(row["expected_assists"])
                            for row in recent) / recent_minutes
    points = [max(0.0, _number(row.get("total_points")) or 0.0) for row in ordered]
    best = max(range(len(ordered)), key=lambda index: points[index])
    share = points[best] / max(1.0, sum(points))
    dependency = (len(ordered) >= 5 and share >= 0.60
                  and int(ordered[best]["gw"]) < target_gw - 3
                  and season_xgi > 0 and recent_xgi < 0.60 * season_xgi)
    evidence.update({
        "status": "applied", "sample_minutes": minutes,
        "half_life_gws": 3, "recent_window_gws": 4, "recent_weight": 0.70,
        "season_xgi_per90": round(season_xgi, 3),
        "recent_xgi_per90": round(recent_xgi, 3),
        "adjusted_xgi_per90": round(rates["expected_goals"] + rates["expected_assists"], 3),
        "best_points_gw": int(ordered[best]["gw"]),
        "best_points_share": round(share, 3),
        "single_haul_dependency": dependency,
    })
    return rates, evidence
