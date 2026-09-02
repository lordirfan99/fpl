"""Three-state minutes hurdle for the non-executable V4.2 candidate."""
from dataclasses import asdict, dataclass


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class MinutesForecastV42:
    p_dnp: float
    p_1_59: float
    p_60_plus: float
    p_appear: float
    p_start: float
    expected_minutes: float
    confidence: float
    signals: dict

    def as_dict(self):
        return asdict(self)


def forecast_minutes_v42(element, history=None, *, congestion=False,
                         team_rotation=0.0):
    history = list(history or [])[-8:]
    status = element.get("status", "a")
    cop = element.get("chance_of_playing_next_round")
    if status in ("i", "u", "s") or (cop is not None and _f(cop) < 25):
        return MinutesForecastV42(1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0,
                                  {"availability": "unavailable", "sample_gws": len(history)})

    # Dirichlet-style prior: useful early, then recent official evidence takes over.
    counts = [0.48, 0.72, 2.80]
    weighted_minutes_1_59 = []
    weighted_minutes_60 = []
    for age, row in enumerate(reversed(history)):
        weight = 0.82 ** age
        minutes = _f(row.get("minutes"))
        if minutes <= 0:
            counts[0] += weight
        elif minutes < 60:
            counts[1] += weight
            weighted_minutes_1_59.append((minutes, weight))
        else:
            counts[2] += weight
            weighted_minutes_60.append((minutes, weight))

    total = sum(counts)
    p_dnp, p_short, p_long = (value / total for value in counts)
    rotation = max(0.0, min(0.35, _f(team_rotation)))
    if congestion:
        rotation = min(0.40, rotation + 0.10)
    moved = p_long * rotation
    p_long -= moved
    p_short += moved * 0.55
    p_dnp += moved * 0.45

    availability = 1.0
    if cop is not None:
        availability *= max(0.0, min(1.0, _f(cop) / 100.0))
    news = (element.get("news") or "").strip()
    if news:
        availability *= 0.88
    unavailable_mass = (1.0 - availability) * (p_short + p_long)
    p_short *= availability
    p_long *= availability
    p_dnp += unavailable_mass
    norm = max(1e-9, p_dnp + p_short + p_long)
    p_dnp, p_short, p_long = p_dnp / norm, p_short / norm, p_long / norm

    def weighted_mean(values, fallback):
        den = sum(w for _, w in values)
        return sum(v * w for v, w in values) / den if den else fallback

    short_minutes = max(8.0, min(55.0, weighted_mean(weighted_minutes_1_59, 26.0)))
    long_minutes = max(60.0, min(90.0, weighted_mean(weighted_minutes_60, 80.0)))
    expected = p_short * short_minutes + p_long * long_minutes
    sample = len(history)
    confidence = availability * (0.30 + 0.70 * sample / (sample + 6.0))
    signals = {
        "sample_gws": sample,
        "recent_60_plus": sum(_f(r.get("minutes")) >= 60 for r in history[-4:]),
        "news": bool(news), "availability": round(availability, 4),
        "congestion": bool(congestion), "team_rotation": round(rotation, 4),
    }
    return MinutesForecastV42(
        round(p_dnp, 5), round(p_short, 5), round(p_long, 5),
        round(p_short + p_long, 5), round(p_long + 0.35 * p_short, 5),
        round(expected, 2), round(max(0.05, min(1.0, confidence)), 4), signals,
    )
