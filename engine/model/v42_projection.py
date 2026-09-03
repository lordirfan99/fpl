"""Official-FPL-only Competitive V4.2 shadow projection.

This module is deliberately separate from the executable V4.1 facade.
"""
from __future__ import annotations

import datetime
import math

from calibration_v42 import calibrate
from feature_store_v42 import fixture_factors, player_rates, team_rotation_rate
from minutes_v42 import forecast_minutes_v42


POS_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
GOAL_POINTS = {"GKP": 10, "DEF": 6, "MID": 5, "FWD": 4}
CS_POINTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}
PRIORS = {
    "GKP": {"goals_scored": 0.00, "assists": 0.01, "expected_goals": 0.00,
            "expected_assists": 0.01, "saves": 3.0, "yellow_cards": 0.05,
            "red_cards": 0.005, "defensive_contribution": 0.0},
    "DEF": {"goals_scored": 0.06, "assists": 0.08, "expected_goals": 0.06,
            "expected_assists": 0.07, "saves": 0.0, "yellow_cards": 0.14,
            "red_cards": 0.01, "defensive_contribution": 7.0},
    "MID": {"goals_scored": 0.22, "assists": 0.20, "expected_goals": 0.20,
            "expected_assists": 0.18, "saves": 0.0, "yellow_cards": 0.12,
            "red_cards": 0.008, "defensive_contribution": 5.0},
    "FWD": {"goals_scored": 0.34, "assists": 0.14, "expected_goals": 0.32,
            "expected_assists": 0.13, "saves": 0.0, "yellow_cards": 0.10,
            "red_cards": 0.008, "defensive_contribution": 2.0},
}


def _clamp(value, low, high):
    return max(low, min(high, float(value)))


def _logistic(value, midpoint, steepness):
    """P(hit) that a per-90 rate clears an FPL threshold. 0.5 at the threshold,
    not the old hard clamp that treated 'averages the threshold' as certain."""
    return 1.0 / (1.0 + math.exp(-float(steepness) * (float(value) - float(midpoint))))


def _congested(fixture_map, team, gw):
    current = fixture_map.get((gw, team), [])
    if len(current) > 1:
        return True
    times = []
    for (event, club), fixtures in fixture_map.items():
        if club != team or abs(int(event) - int(gw)) > 1:
            continue
        for fixture in fixtures:
            try:
                times.append(datetime.datetime.fromisoformat(
                    str(fixture.get("kickoff_time")).replace("Z", "+00:00")))
            except (TypeError, ValueError):
                pass
    times.sort()
    return any((right - left).total_seconds() < 4.5 * 86400
               for left, right in zip(times, times[1:]))


def fixture_projection(element, fixture, history, strengths, *, congestion=False,
                       team_rotation=0.0):
    position = POS_MAP.get(int(element.get("element_type") or 3), "MID")
    minutes = forecast_minutes_v42(element, history, congestion=congestion,
                                   team_rotation=team_rotation)
    if minutes.p_appear <= 0:
        return {"mean": 0.0, "variance": 0.0, "components": {}, "minutes": minutes}
    rates = player_rates(history, PRIORS[position])
    factors = fixture_factors(element["team"], fixture, strengths)
    share = minutes.expected_minutes / 90.0

    # Blend actual and expected attacking rates after exposure shrinkage.
    goal_rate = 0.40 * rates["goals_scored"] + 0.60 * rates["expected_goals"]
    assist_rate = 0.45 * rates["assists"] + 0.55 * rates["expected_assists"]
    # Official FPL set-piece order is most useful before a player has enough
    # event exposure. Decay the role prior as observed xG/xA takes over.
    role_weight = 900.0 / (900.0 + float(rates["sample_minutes"]))
    penalty_order = int(element.get("penalties_order") or 0)
    direct_order = int(element.get("direct_freekicks_order") or 0)
    corner_order = int(element.get("corners_and_indirect_freekicks_order") or 0)
    role_goal_rate = role_weight * (
        (0.06 if penalty_order == 1 else 0.02 if penalty_order == 2 else 0.0)
        + (0.015 if direct_order == 1 else 0.0)
    )
    role_assist_rate = role_weight * (
        0.035 if corner_order == 1 else 0.015 if corner_order == 2 else 0.0
    )
    components = {
        "appearance": minutes.p_1_59 + 2.0 * minutes.p_60_plus,
        "goals": goal_rate * share * GOAL_POINTS[position] * factors["attack"],
        "assists": assist_rate * share * 3.0 * factors["attack"],
        "set_piece_role": (role_goal_rate * GOAL_POINTS[position]
                           + role_assist_rate * 3.0) * share * factors["attack"],
        "clean_sheet": factors["clean_sheet"] * minutes.p_60_plus * CS_POINTS[position],
        "saves": rates["saves"] * share / 3.0 if position == "GKP" else 0.0,
        "defensive": (2.0 * _logistic(rates["defensive_contribution"],
                                      10.0 if position == "DEF" else 12.0, 0.35)
                      * minutes.p_60_plus) if position in ("DEF", "MID") else 0.0,
        "discipline": -(rates["yellow_cards"] + 3.0 * rates["red_cards"]) * share,
    }
    mean = max(0.0, sum(components.values()))
    attack = components["goals"] + components["assists"]
    variance = max(0.35, mean * 0.70 + attack * 2.0 + minutes.p_dnp * 3.5)
    return {"mean": mean, "variance": variance, "components": components,
            "minutes": minutes, "fixture_factors": factors,
            "sample_minutes": rates["sample_minutes"]}


def project_player_v42(element, fixture_map, gw_ids, history_by_element,
                       strengths, calibration=None):
    history = list(history_by_element.get(int(element["id"]), []))
    rotation = team_rotation_rate(history_by_element, int(element["team"]))
    per_gw = []
    for gw in gw_ids:
        fixtures = fixture_map.get((gw, int(element["team"])), [])
        congestion = _congested(fixture_map, int(element["team"]), gw)
        rows = [fixture_projection(element, fixture, history, strengths,
                                   congestion=congestion, team_rotation=rotation)
                for fixture in fixtures]
        if rows:
            mean = sum(r["mean"] for r in rows)
            variance = sum(r["variance"] for r in rows)
            minutes = rows[0]["minutes"]
            components = {}
            for row in rows:
                for key, value in row["components"].items():
                    components[key] = components.get(key, 0.0) + value
        else:
            mean, variance, components = 0.0, 0.0, {}
            minutes = forecast_minutes_v42(element, history)
        per_gw.append((mean, variance, minutes, components))

    if not per_gw:
        per_gw = [(0.0, 0.0, forecast_minutes_v42(element, history), {})]
    means = [row[0] for row in per_gw]
    variances = [row[1] for row in per_gw]
    # Official ep_next is deadline-valid. Keep it as a bounded early-season anchor.
    try:
        official = float(element.get("ep_next") or 0.0)
    except (TypeError, ValueError):
        official = 0.0
    if means and means[0] > 0 and official > 0:
        evidence_gws = len(history)
        weight = max(0.10, 0.40 - 0.05 * evidence_gws)
        means[0] = weight * official + (1.0 - weight) * means[0]

    position = POS_MAP.get(int(element.get("element_type") or 3), "MID")
    mean, variance, floor, median, upside, calibration_sample = calibrate(
        means[0], variances[0], calibration or {}, position
    )
    means[0], variances[0] = mean, variance
    first_minutes = per_gw[0][2]
    weights = (1.0, 0.7, 0.5)
    expected_horizon = sum(w * value for w, value in zip(weights, means))
    confidence = first_minutes.confidence * min(1.0, 0.35 + len(history) / 8.0)
    degraded = []
    if len(history) < 3:
        degraded.append("limited_player_history")
    if calibration_sample < 200:
        degraded.append("uncalibrated_distribution")
    return {
        "model_version": "competitive-v4.2-shadow",
        "mean": round(mean, 3), "xpts": round(mean, 3),
        "variance": round(variance, 3), "floor": round(floor, 3),
        "median": round(median, 3), "upside": round(upside, 3),
        "xpts_by_gw": [round(v, 3) for v in means],
        "variance_by_gw": [round(v, 3) for v in variances],
        "expected_horizon": round(expected_horizon, 3),
        "p_dnp": first_minutes.p_dnp, "p_1_59": first_minutes.p_1_59,
        "p_60_plus": first_minutes.p_60_plus,
        "p_start": first_minutes.p_start,
        "expected_minutes": first_minutes.expected_minutes,
        "confidence": round(max(0.05, min(1.0, confidence)), 3),
        "signals": first_minutes.signals,
        "components": {k: round(v, 3) for k, v in per_gw[0][3].items()},
        "calibration_sample": calibration_sample,
        "degraded_reasons": degraded,
    }
