"""Official-FPL minutes/start probability model for Competitive V4.

The output is intentionally probabilistic and dependency-free so it can be
unit-tested and safely used as a first-class input to any xPts model.
"""
from dataclasses import dataclass


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class MinutesForecast:
    p_start: float
    p_appear: float
    p_60: float
    expected_minutes: float
    rotation_risk: float


def forecast_minutes(el, gw_so_far, congestion=False):
    """Estimate start/appearance/60+ probabilities and expected minutes.

    Uses only live bootstrap fields, so this is a robust baseline even when
    richer event-history data is unavailable. Injury/unavailable status fails
    closed to zero. Congestion is an optional external signal.
    """
    status = el.get("status", "a")
    cop = el.get("chance_of_playing_next_round")
    if status in ("i", "u", "s") or (cop is not None and cop < 25):
        return MinutesForecast(0.0, 0.0, 0.0, 0.0, 1.0)

    starts = _f(el.get("starts"))
    minutes = _f(el.get("minutes"))
    games = max(0.0, float(gw_so_far or 0))

    # A 75% starter prior prevents GW1 from pretending that every player is a
    # 50/50 start. Official starts and minute share then take over gradually.
    # One completed 90-minute start yields ~79%, while a GW1 non-starter stays
    # below the captain gate. This is deliberately conservative but usable.
    if games:
        start_rate = min(1.0, starts / games)
        minute_share = min(1.0, minutes / (games * 90.0))
        evidence = max(start_rate, 0.95 * minute_share)
        evidence_weight = games / (games + 4.0)
        p_start = 0.75 * (1.0 - evidence_weight) + evidence * evidence_weight
    else:
        minute_share = 0.0
        p_start = 0.82
    p_appear = min(0.98, max(p_start, 0.35 + 0.63 * minute_share))

    # Availability/news discounts are deliberately smooth rather than binary.
    avail = 1.0
    if cop is not None:
        avail *= max(0.0, min(1.0, float(cop) / 100.0))
    if (el.get("news") or "").strip():
        avail *= 0.88

    rotation_risk = max(0.0, 1.0 - p_start)
    if congestion:
        rotation_risk = min(1.0, rotation_risk + 0.12)
        p_start *= 0.88

    p_start *= avail
    p_appear *= avail
    p_60 = max(0.0, min(p_start * 0.9, p_appear))

    # Use the player's official average start length once observed. This avoids
    # an impossible 65-minute captain gate after a genuine 90-minute GW1 start.
    observed_start_minutes = minutes / starts if starts > 0 else 82.0
    start_minutes = max(70.0, min(90.0, observed_start_minutes))
    expected = p_start * start_minutes + max(0.0, p_appear - p_start) * 20.0
    expected = max(0.0, min(90.0, expected))
    return MinutesForecast(
        round(max(0.0, min(1.0, p_start)), 4),
        round(max(0.0, min(1.0, p_appear)), 4),
        round(max(0.0, min(1.0, p_60)), 4),
        round(expected, 2),
        round(max(0.0, min(1.0, rotation_risk)), 4),
    )
