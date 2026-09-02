"""Manager sharpness scoring for the RM3000 league.

Measures which opposing managers are sharpest at picking players, so we can
track and beat them. Composite 0-100 across four dimensions:

  captain_efficiency  (40%): how often their captain choice beats the field /
                           best-available option
  transfer_efficiency (25%): points gained from transfers vs hits paid
  value_capture       (15%): team value growth (price rises banked)
  consistency         (20%): GW-rank percentile stability (not one lucky GW)

PRE-SEASON REALITY: with 0 completed GWs the score is a PRIOR, not evidence.
The system supports a `prior` input (e.g. historical finish from the league
admin or an informed 50 default) and explicitly labels confidence as low until
min_gws_completed (default 4) GWs of live data exist.

API source per manager: entry/{id}/history/ -> current[] (per-GW points, rank,
transfers_cost, bank, value, event_transfers) + past[] (season summaries).

Output: {entry_id: {score, components, confidence, gws_evaluated}}.
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Composite weights (Sol orchestrator directive, docs/sol-directive-league-monitor.md)
WEIGHTS = {
    "captain_efficiency": 0.30,
    "transfer_efficiency": 0.35,
    "value_capture": 0.10,
    "consistency": 0.25,
}

# Trust labels per Sol: 0 = prior only; 1-3 low; 4-5 provisional; 6-11 trusted; 12+ established
def trust_label(gws):
    if gws <= 0:
        return "preseason prior only"
    if gws <= 3:
        return "low"
    if gws <= 5:
        return "provisional"
    if gws <= 11:
        return "trusted"
    return "established"


def captain_efficiency(history):
    """How often their captain (multiplier 2 pick) outscores their non-captain
    best pick. Data: we only get total GW points from history, so we proxy with
    the captain share heuristic:
      cap_eff = clamp(1 - (gw_points_cap_share), ...) — see note.
    TRUE captain-vs-best needs picks API per GW; use history 'points' vs the
    'best captain' from event live when available. Default neutral 0.5.
    """
    # With history-only data, we cannot isolate captain performance without
    # picks. Use a neutral prior; the league_monitor with live picks upgrades
    # this component post-GW.
    return 0.5


def transfer_efficiency(history):
    """Points from transfers vs hits. history current[] rows have
    'event_transfers' and 'transfers_cost'. We approximate efficiency as
    1 - (total transfers_cost / total points), clipped."""
    rows = history or []
    total_cost = sum(float(r.get("transfers_cost", 0) or 0) for r in rows)
    total_pts = sum(float(r.get("points", 0) or 0) for r in rows)
    if total_pts <= 0:
        return 0.5
    return max(0.0, min(1.0, 1.0 - total_cost / max(total_pts, 1e-9)))


def value_capture(history):
    """Team value growth vs starting £100m. current[] rows carry 'value'."""
    rows = history or []
    if not rows:
        return 0.5
    last_value = float(rows[-1].get("value", 1000) or 1000) / 10.0  # to £m
    growth = last_value - 100.0
    # +£5m value growth over a season = excellent (1.0); 0 = neutral (0.5)
    return max(0.0, min(1.0, 0.5 + growth / 10.0))


def consistency(history):
    """GW rank percentile stability. Lower std of rank = more consistent.
    history current[] rows carry 'rank' (overall) or 'overall_rank'."""
    rows = history or []
    if len(rows) < 2:
        return 0.5
    import statistics
    ranks = [float(r.get("rank", r.get("overall_rank", 0)) or 0) for r in rows]
    ranks = [r for r in ranks if r > 0]
    if len(ranks) < 2:
        return 0.5
    cv = statistics.stdev(ranks) / max(statistics.mean(ranks), 1e-9)
    # cv 0.1 = very consistent -> 1.0; cv 1.0 = erratic -> 0.0
    return max(0.0, min(1.0, 1.0 - cv))


def preseason_prior(past_seasons=None):
    """Prior from past-season finishes (Sol: 100*(1-sqrt(r/N)), 60/30/10 blend).

    past_seasons: list of {rank, total_players or N} newest-first, or None.
    """
    if not past_seasons:
        return 50.0, "Unknown history"
    weights = [0.6, 0.3, 0.1][:len(past_seasons)]
    wsum = sum(weights)
    score = 0.0
    for w, s in zip(weights, past_seasons):
        r = float(s.get("rank", 0) or 0)
        n = float(s.get("total_players") or s.get("N") or 0)
        if r <= 0 or n <= 0:
            continue
        score += (w / wsum) * max(0.0, min(100.0, 100.0 * (1.0 - (r / n) ** 0.5)))
    return round(score, 1), "past-season finish prior"


def score_manager(history=None, prior=50.0, past_seasons=None):
    """Composite sharpness 0-100 with components + confidence label.

    Sol posterior shrinkage: Sharpness = P0 + [n/(n+4)]*(Sraw - P0).
    """
    history = history or []
    gws = len(history)
    if prior is None and past_seasons:
        prior, _ = preseason_prior(past_seasons)
    if prior is None:
        prior = 50.0
    components = {
        "captain_efficiency": captain_efficiency(history),
        "transfer_efficiency": transfer_efficiency(history),
        "value_capture": value_capture(history),
        "consistency": consistency(history),
    }
    sraw = 0.0
    for k, w in WEIGHTS.items():
        sraw += w * components[k]
    sraw *= 100.0
    # posterior shrinkage toward prior with n_eff = completed GWs
    n_eff = float(gws)
    shrink = n_eff / (n_eff + 4.0) if n_eff > 0 else 0.0
    score = prior + shrink * (sraw - prior)
    return {
        "score": round(score, 1),
        "components": {k: round(v * 100, 1) for k, v in components.items()},
        "confidence": trust_label(gws),
        "gws_evaluated": gws,
        "prior": round(prior, 1),
        "shrinkage": round(shrink, 2),
        "sraw": round(sraw, 1),
    }


def sharpest_managers(manager_histories, top_n=3, prior=50.0, past_seasons=None):
    """manager_histories: {entry_id: history_rows}. Returns sorted list."""
    scored = []
    for entry_id, hist in manager_histories.items():
        ps = (past_seasons or {}).get(entry_id)
        s = score_manager(hist, prior=prior, past_seasons=ps)
        scored.append({"entry_id": entry_id, **s})
    scored.sort(key=lambda x: -x["score"])
    return scored[:top_n]


if __name__ == "__main__":
    print(json.dumps(score_manager([]), indent=1))
