"""Component-based expected points model (v3).

This module decomposes FPL scoring into interpretable components and returns
both mean xPts and uncertainty. It deliberately uses robust bootstrap proxies
when richer xG/xA sources are unavailable; v2 remains a fallback upstream.
"""
import math
from dataclasses import dataclass, asdict
from minutes_model import forecast_minutes

POS_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
GOAL_POINTS = {"GKP": 10, "DEF": 6, "MID": 5, "FWD": 4}
CS_POINTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}
GOAL_PRIOR_90 = {"GKP": 0.0, "DEF": 0.04, "MID": 0.18, "FWD": 0.30}
ASSIST_PRIOR_90 = {"GKP": 0.01, "DEF": 0.06, "MID": 0.16, "FWD": 0.12}
BONUS_PRIOR_90 = {"GKP": 0.22, "DEF": 0.24, "MID": 0.30, "FWD": 0.28}
CS_PRIOR = {"GKP": 0.30, "DEF": 0.30, "MID": 0.27, "FWD": 0.0}


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _rate(total, minutes):
    return _f(total) * 90.0 / max(90.0, _f(minutes))


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _shrink_rate(observed, minutes, prior, prior_minutes=900.0):
    """Bayesian-style early-season shrinkage toward a position prior."""
    exposure = max(0.0, _f(minutes))
    return (observed * exposure + prior * prior_minutes) / (exposure + prior_minutes)


@dataclass(frozen=True)
class XPtsForecast:
    mean: float
    floor: float
    upside: float
    variance: float
    p_start: float
    expected_minutes: float
    components: dict

    def as_dict(self):
        return asdict(self)


def fixture_xpts(el, fixture, gw_so_far, odds_multiplier=1.0, congestion=False):
    """Expected points for ONE fixture.

    Bootstrap fields expected_goals/assists/goal_involvements/clean_sheets,
    bonus, saves and defensive_contribution are consumed when present. Missing
    fields safely degrade to season-rate proxies.
    """
    pos = POS_MAP.get(el.get("element_type"), "MID")
    mf = forecast_minutes(el, gw_so_far, congestion=congestion)
    if mf.p_appear <= 0:
        return XPtsForecast(0, 0, 0, 0, 0, 0, {})

    mins = _f(el.get("minutes"))
    season_games = max(1.0, float(gw_so_far or 1))
    fixture_factor = _clamp(float(odds_multiplier or 1.0), 0.65, 1.45)
    fdr = float(fixture.get("fdr", 3) or 3)
    # Odds is preferred; FDR is a weak residual if no odds differentiation.
    fdr_factor = _clamp(1.08 - 0.04 * (fdr - 2), 0.82, 1.08)
    attack_factor = fixture_factor * fdr_factor

    goals90 = _rate(el.get("goals_scored"), mins)
    assists90 = _rate(el.get("assists"), mins)
    raw_bonus90 = _rate(el.get("bonus"), mins)
    raw_saves90 = _rate(el.get("saves"), mins)
    yellow90 = _rate(el.get("yellow_cards"), mins)
    red90 = _rate(el.get("red_cards"), mins)

    # Expected-stat fields improve rates when FPL exposes them.
    xg90 = _rate(el.get("expected_goals"), mins)
    xa90 = _rate(el.get("expected_assists"), mins)
    raw_goal_rate = goals90 * 0.55 + xg90 * 0.45
    raw_assist_rate = assists90 * 0.60 + xa90 * 0.40
    goal_rate = _shrink_rate(raw_goal_rate, mins, GOAL_PRIOR_90[pos])
    assist_rate = _shrink_rate(raw_assist_rate, mins, ASSIST_PRIOR_90[pos])
    bonus90 = _shrink_rate(raw_bonus90, mins, BONUS_PRIOR_90[pos])
    saves90 = _shrink_rate(raw_saves90, mins, 3.0 if pos == "GKP" else 0.0)

    minute_share = mf.expected_minutes / 90.0
    appearance = mf.p_appear + mf.p_60  # 1 for appearance + extra 1 at 60
    goals = goal_rate * minute_share * GOAL_POINTS[pos] * attack_factor
    assists = assist_rate * minute_share * 3.0 * attack_factor

    # CS probability proxy from historical clean-sheet frequency, adjusted by
    # fixture difficulty. This is deliberately conservative and calibratable.
    # Eight prior matches prevent one early result from dominating the model.
    cs_rate = (_f(el.get("clean_sheets")) + CS_PRIOR[pos] * 8.0) / (season_games + 8.0)
    team_cs_proxy = _clamp(cs_rate / max(0.15, mf.p_60), 0.05, 0.65)
    defence_factor = _clamp(2.0 - attack_factor, 0.65, 1.35)
    p_cs = _clamp(team_cs_proxy * defence_factor, 0.03, 0.65) * mf.p_60
    clean_sheet = p_cs * CS_POINTS[pos]

    save_pts = 0.0
    if pos == "GKP":
        expected_saves = saves90 * minute_share / 3.0
        save_pts = expected_saves

    bonus = bonus90 * minute_share * _clamp(0.8 + 0.2 * attack_factor, 0.7, 1.2)

    # 2026/27 defensive contribution proxy. If the explicit field exists use
    # its per-90 rate; otherwise use a conservative baseline by position.
    dc_total = _f(el.get("defensive_contribution"))
    if dc_total:
        dc90 = _rate(dc_total, mins)
        threshold = 10.0 if pos == "DEF" else 12.0
        p_dc = _clamp(dc90 / threshold, 0.0, 1.0) * mf.p_60
    else:
        p_dc = {"GKP": 0.0, "DEF": 0.24, "MID": 0.10, "FWD": 0.03}[pos] * mf.p_60
    defensive = 2.0 * p_dc

    discipline = -(yellow90 + 3.0 * red90) * minute_share

    comps = {
        "appearance": appearance,
        "goals": goals,
        "assists": assists,
        "clean_sheet": clean_sheet,
        "saves": save_pts,
        "bonus": bonus,
        "defensive": defensive,
        "discipline": discipline,
    }
    mean = max(0.0, sum(comps.values()))

    # Simple heteroscedastic uncertainty: attacking returns and start risk are
    # the main variance sources. The values are decision features, not CIs.
    attack_mean = goals + assists + bonus
    variance = max(0.5, mean * 0.75 + attack_mean * 1.6 + (1.0 - mf.p_start) * 4.0)
    sd = math.sqrt(variance)
    floor = max(0.0, mean - 0.85 * sd)
    upside = mean + 1.65 * sd
    return XPtsForecast(round(mean, 3), round(floor, 3), round(upside, 3),
                        round(variance, 3), mf.p_start, mf.expected_minutes,
                        {k: round(v, 3) for k, v in comps.items()})


def gameweek_xpts(el, fixtures, gw_so_far, odds_multiplier_for_fixture=None, congestion=False):
    """Aggregate one or many fixtures. Empty list = true BGW xPts 0."""
    if not fixtures:
        return XPtsForecast(0, 0, 0, 0, 0, 0, {})
    forecasts = []
    for fx in fixtures:
        mult = odds_multiplier_for_fixture(fx) if odds_multiplier_for_fixture else 1.0
        forecasts.append(fixture_xpts(el, fx, gw_so_far, mult, congestion))
    components = {}
    for fc in forecasts:
        for k, v in fc.components.items():
            components[k] = components.get(k, 0.0) + v
    mean = sum(f.mean for f in forecasts)
    variance = sum(f.variance for f in forecasts)
    sd = math.sqrt(max(0.0, variance))
    p_start = max((f.p_start for f in forecasts), default=0.0)
    expected_minutes = sum(f.expected_minutes for f in forecasts)
    return XPtsForecast(round(mean, 3), round(max(0.0, mean - 0.85 * sd), 3),
                        round(mean + 1.65 * sd, 3), round(variance, 3),
                        p_start, round(expected_minutes, 2),
                        {k: round(v, 3) for k, v in components.items()})
