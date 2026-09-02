"""
FPL Autopilot - xPts model v1 (validated).

xPts = play_probability (Bayesian) x max(form_per90, position_baseline) x opponent FDR adjust

Why this shape (validated on 2025-26 backtest):
  - minutes/play probability is the dominant predictor (Spearman ~0.42 alone)
  - raw per-90 form is NEGATIVELY correlated with next-GW points (-0.13) once
    cameo games and bonus explosions are included; after filtering to >=20min
    games and clipping [0,25] it becomes positive (+0.27)
  - max(form, position_baseline) x mp scored 0.60 Spearman in the diagnostic;
    using a prior-season baseline removes the lookahead bias
Phase 2 will add xG-based rates, clean-sheet Poisson, and defensive contribution.
"""

import json
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_WEIGHTS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.25]  # oldest -> newest, sum 1.0
_MIN_MINUTES = 20   # ignore cameo games in rate calc
_RATE_CAP = 25.0    # clip per-90 rate (red-card/hat-trick outliers)

_DEFAULT_BASELINES = {"GKP": 3.35, "GK": 3.35, "DEF": 2.92, "MID": 3.96, "FWD": 4.97}


def _baselines():
    path = os.path.join(_THIS_DIR, "position_baselines.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return dict(_DEFAULT_BASELINES)


def min_probability(minutes_list):
    """Bayesian-smoothed probability of playing (any minutes) in next GW."""
    minutes_list = [m for m in minutes_list if m is not None]
    if not minutes_list:
        return 0.5
    played = sum(1 for m in minutes_list if m > 0)
    return (played + 1.0) / (len(minutes_list) + 2.0)


def per90_rate(points_list, minutes_list):
    """Recent-weighted per-90 rate, ignoring cameos, clipped."""
    rates = []
    for pts, mins in zip(points_list, minutes_list):
        if mins and mins >= _MIN_MINUTES:
            rates.append(max(0.0, min(_RATE_CAP, pts * 90.0 / mins)))
    if not rates:
        return None
    n = len(rates)
    w = _WEIGHTS[-n:] if n <= len(_WEIGHTS) else [1.0 / n] * n
    return sum(r * ww for r, ww in zip(rates, w)) / sum(w)


def position_baseline(position):
    b = _baselines()
    return b.get(position) or b.get("GK" if position in ("GKP",) else position, 3.5)


def fdr_multiplier(fdr, position):
    if position in ("GKP", "GK", "DEF"):
        return 1.0
    if fdr <= 2:
        return 1.0
    return max(0.75, 1.0 - 0.05 * (fdr - 2))


def predict(points_list, minutes_list, fdr=3, position="MID"):
    mp = min_probability(minutes_list)
    rate = per90_rate(points_list, minutes_list)
    base = position_baseline(position)
    eff = max(rate, base) if rate is not None else base
    return eff * mp * fdr_multiplier(fdr, position)


def inseason_xpts_from_bootstrap(el, fdr, gw_so_far):
    """In-season expected points from live bootstrap fields.

    Uses the SAME validated shape as predict(): play probability x
    max(rate, position baseline) x FDR, plus the injury gate that was
    missing pre-season (status/cop/news). Rate blends form (last 5 GWs,
    recent-weighted) with season ppg.
    """
    status = el.get("status", "a")
    cop = el.get("chance_of_playing_next_round")
    news = (el.get("news") or "").strip()

    # hard injury gate - includes status 'u' (unavailable: left the league /
    # gone for the season) which is DIFFERENT from 'i' (injured, might return).
    # Edge-case B11: a player permanently gone must be zeroed and force a
    # transfer OUT regardless of cost; treating him like a recoverable injury
    # leaves the optimizer stuck (all replacements worse than a phantom).
    if status in ("i", "u") or (cop is not None and cop < 50):
        return 0.0

    position = _POS_MAP.get(el.get("element_type"), "MID")
    ppg = _fnum(el.get("points_per_game"))
    form = _fnum(el.get("form"))
    minutes = _fnum(el.get("minutes"))

    # play probability from season minutes share
    max_min = max(1.0, gw_so_far * 90.0)
    mp = min(0.92, 0.3 + 0.6 * (minutes / max_min))
    # soft injury penalty: 50-75 cop or any news text -> halve
    if (cop is not None and cop < 75) or news:
        mp *= 0.5

    # recent-weighted rate: form (last 5) gets 2x weight vs season ppg
    rate = max(0.67 * form + 0.33 * ppg, position_baseline(position))
    return rate * mp * fdr_multiplier(fdr, position)


_POS_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
_MAX_MINUTES = 3420.0  # 38 GWs x 90 min


def preseason_xpts(el, fdr):
    """Pre-season expected points proxy for GW1 (form resets to 0, so we use
    last season's points_per_game + minutes, with the injury gate)."""
    status = el.get("status", "a")
    cop = el.get("chance_of_playing_next_round")
    news = (el.get("news") or "").strip()

    if status in ("i", "u") or (cop is not None and cop < 50):
        return 0.0
    ppg = _fnum(el.get("points_per_game"))
    minutes = _fnum(el.get("minutes"))
    position = _POS_MAP.get(el.get("element_type"), "MID")
    mp = min(0.9, 0.3 + 0.6 * (minutes / _MAX_MINUTES))
    if (cop is not None and cop < 75) or news:
        mp *= 0.5
    rate = max(ppg, position_baseline(position))
    return rate * mp * fdr_multiplier(fdr, position)


def _fnum(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
