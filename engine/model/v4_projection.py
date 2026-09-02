"""Odds-free V4 projection facade.

V4 is the live decision model: official FPL data, fixture difficulty,
expected minutes and component scoring with an explicit uncertainty penalty.
Betting odds are deliberately not read here; historical odds remain usable
only by offline research/backtests.
"""
from component_xpts import gameweek_xpts


def captain_score(player):
    """Captain utility: mean plus bounded ceiling, with a small role tiebreak.

    Captaincy doubles one player's outcome, so near-equal means should favour
    attacking ceiling instead of a goalkeeper's narrow clean-sheet edge.
    """
    mean = float(player.get("xpts") or 0.0)
    upside = float(player.get("xpts_upside") or mean)
    role_bonus = {"FWD": 0.15, "MID": 0.12, "DEF": 0.0, "GKP": -0.15}.get(
        player.get("position"), 0.0
    )
    return mean + 0.20 * max(0.0, upside - mean) + role_bonus


def captain_rankings(starters, captain_id=None, min_start=0.75, min_minutes=65.0):
    """Transparent captain evidence; ownership is deliberately not a score input."""
    rows = []
    for player in starters:
        p_start = float(player.get("p_start") or 0.0)
        minutes = float(player.get("expected_minutes") or 0.0)
        eligible = p_start >= min_start and minutes >= min_minutes
        reasons = []
        if p_start < min_start:
            reasons.append(f"start probability {p_start:.0%} below {min_start:.0%}")
        if minutes < min_minutes:
            reasons.append(f"expected minutes {minutes:.0f} below {min_minutes:.0f}")
        rows.append({
            "id": player.get("id"), "name": player.get("name"),
            "position": player.get("position"), "xpts": round(float(player.get("xpts") or 0), 2),
            "score": round(captain_score(player), 3), "p_start": round(p_start, 3),
            "expected_minutes": round(minutes, 1), "eligible": eligible,
            "selected": player.get("id") == captain_id,
            "reason": "eligible on points, ceiling and minutes" if eligible else "; ".join(reasons),
        })
    return sorted(rows, key=lambda row: (not row["eligible"], -row["score"]))


def select_vice(starters, captain, min_start=0.75, min_minutes=65.0):
    """Prefer a reliable vice from another club to reduce shared disruption risk."""
    choices = [player for player in starters if player.get("id") != captain.get("id")]
    reliable = [player for player in choices
                if float(player.get("p_start") or 0) >= min_start
                and float(player.get("expected_minutes") or 0) >= min_minutes]
    pool = reliable or choices
    diversified = [player for player in pool if player.get("club") != captain.get("club")]
    return max(diversified or pool, key=captain_score) if pool else captain


def project_player(element, fixtures_by_gw, gw_so_far, gw_ids,
                   uncertainty_multiplier=1.0):
    forecasts = [gameweek_xpts(element, fixtures_by_gw.get((gw, element["team"]), []), gw_so_far)
                 for gw in gw_ids]
    means = [f.mean for f in forecasts]
    uncertainty_multiplier = max(0.75, min(1.75, float(uncertainty_multiplier)))
    variances = [f.variance * uncertainty_multiplier ** 2 for f in forecasts]
    # FPL's official next-GW estimate is a useful preseason/early-season
    # anchor. Blend it only into the immediate GW and decay its influence as
    # our own sample grows; this prevents one goal/clean sheet from turning a
    # defender into an implausible captain after GW1.
    try:
        official_next = float(element.get("ep_next") or 0.0)
    except (TypeError, ValueError):
        official_next = 0.0
    official_weight = max(0.15, 0.75 - 0.10 * max(0, gw_so_far))
    if means and official_next > 0:
        means[0] = official_weight * official_next + (1.0 - official_weight) * means[0]
    weights = [1.0, 0.7, 0.5]
    expected_horizon = sum(w * x for w, x in zip(weights, means))
    risk_by_gw = [max(0.0, mean - 0.25 * (variance ** 0.5))
                  for mean, variance in zip(means, variances)]
    risk_horizon = sum(w * x for w, x in zip(weights, risk_by_gw))
    first = forecasts[0] if forecasts else gameweek_xpts(element, [], gw_so_far)
    status = element.get("status")
    chance = element.get("chance_of_playing_next_round")
    chance = float(chance) / 100.0 if chance is not None else None
    # Confidence is transparent and conservative: availability and sample size
    # determine how much of the point estimate may be trusted.
    availability = first.p_start
    if chance is not None:
        availability *= max(0.0, min(1.0, chance))
    sample = min(1.0, float(element.get("minutes") or 0) / 900.0)
    confidence = max(0.15, min(1.0, 0.65 * availability + 0.35 * sample))
    risk_adjusted = risk_by_gw[0] if risk_by_gw else 0.0
    first_mean = means[0] if means else 0.0
    first_sd = variances[0] ** 0.5 if variances else 0.0
    return {
        "xpts": round(first_mean, 3),
        # Transfer optimization consumes xpts_horizon, so uncertainty is
        # charged directly in the decision objective, not merely displayed.
        "xpts_horizon": round(risk_horizon, 3),
        "expected_horizon": round(expected_horizon, 3),
        "xpts_floor": round(max(0.0, first_mean - 0.85 * first_sd), 3),
        "xpts_upside": round(first_mean + 1.65 * first_sd, 3),
        "xpts_variance": variances[0] if variances else 0.0,
        "uncertainty_multiplier": round(uncertainty_multiplier, 3),
        "expected_minutes": first.expected_minutes,
        "p_start": first.p_start, "confidence": round(confidence, 3),
        "risk_adjusted_xpts": round(risk_adjusted, 3),
        "risk_adjusted_by_gw": [round(x, 3) for x in risk_by_gw],
        "xpts_by_gw": means, "variance_by_gw": variances,
        "components": first.components, "status": status,
        "cop": element.get("chance_of_playing_next_round"), "news": element.get("news"),
    }
