"""Chip opportunity-cost scoring for Intelligence Engine v3."""

CHIP_PRIOR = {
    "3xc": 7.0,
    "bboost": 10.0,
    "freehit": 13.0,
    "wildcard": 16.0,
}


def score_chip_now(chip, plan, fixture_map, gw):
    starters = plan.get("target_starters", [])
    bench = plan.get("bench", [])
    cap = plan.get("captain") or {}
    if chip == "3xc":
        # TC incremental value over normal captaincy = one extra captain score.
        return float(cap.get("xpts", 0.0))
    if chip == "bboost":
        return sum(float(p.get("xpts", 0.0)) for p in bench)
    if chip == "freehit":
        # Proxy: value avoiding blanks/weak XI. Exact FH optimization can replace later.
        blanks = sum(1 for p in starters + bench if not fixture_map.get((gw, p.get("club")), []))
        return blanks * 2.8
    if chip == "wildcard":
        # Proxy: current plan's multi-week transfer improvement.
        return max(0.0, float(plan.get("horizon_gain", 0.0)))
    return 0.0


def opportunity_cost_decision(chip, plan, fixture_map, gw, future_opportunities=None,
                              safety_margin=1.5):
    """Compare chip value now against best plausible future opportunity."""
    now = score_chip_now(chip, plan, fixture_map, gw)
    future = list(future_opportunities or [])
    benchmark = max([CHIP_PRIOR.get(chip, 0.0)] + [float(x) for x in future])
    edge = now - benchmark
    return {
        "chip": chip,
        "value_now": round(now, 2),
        "future_benchmark": round(benchmark, 2),
        "edge": round(edge, 2),
        "play": edge >= safety_margin,
        "reason": (f"play: current value {now:.1f} beats future benchmark {benchmark:.1f}"
                   if edge >= safety_margin else
                   f"hold: current value {now:.1f} does not clear future benchmark {benchmark:.1f} + margin"),
    }
