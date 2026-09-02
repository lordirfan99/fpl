"""Pure feature builders for prize-league intelligence.

Every function treats public FPL payloads as untrusted input and fails soft.
The outputs are advisory signals; none can bypass projections, squad legality,
or the approval/execution boundary.
"""

from __future__ import annotations

import datetime as dt
import math
import random
import statistics
from collections import defaultdict


STANDARD_CHIPS = {"wildcard", "freehit", "bboost", "3xc"}


def _number(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def manager_activity(history_payload, transfers, deadlines=None):
    """Summarize hits, value, chips, and transfer timing for one rival."""
    history_payload = history_payload if isinstance(history_payload, dict) else {}
    current = history_payload.get("current", []) or []
    transfers = transfers if isinstance(transfers, list) else []
    chips = history_payload.get("chips", []) or []
    used = sorted({str(row.get("name")) for row in chips if row.get("name")})
    hits = sum(int(_number(row.get("transfers_cost"), 0)) for row in current)
    transfer_count = len(transfers)
    early = 0
    deadlines = deadlines or {}
    for row in transfers:
        try:
            event = int(row.get("event"))
            stamp = dt.datetime.fromisoformat(str(row.get("time")).replace("Z", "+00:00"))
            deadline = deadlines.get(event)
            if deadline and stamp <= deadline - dt.timedelta(hours=24):
                early += 1
        except Exception:
            continue
    value = _number(current[-1].get("value"), 0) / 10.0 if current else None
    bank = _number(current[-1].get("bank"), 0) / 10.0 if current else None
    avg = transfer_count / max(1, len(current))
    if hits >= 12 or avg >= 1.8:
        archetype = "Aggressive Chaser"
    elif transfer_count and early / transfer_count >= 0.6:
        archetype = "Early-transfer Price Hunter"
    elif transfer_count <= max(1, len(current) // 2):
        archetype = "Patient Optimizer"
    else:
        archetype = "Balanced Manager"
    return {
        "transfer_count": transfer_count,
        "early_transfer_count": early,
        "hits_paid": hits,
        "team_value": round(value, 1) if value is not None else None,
        "bank": round(bank, 1) if bank is not None else None,
        "chips_used": used,
        "chips_unseen": sorted(STANDARD_CHIPS - set(used)),
        "archetype_live": archetype,
    }


def transfer_consensus(cohort, transfers_by_entry, event, element_names=None):
    """Weighted cohort transfer-in/out consensus for a completed deadline."""
    element_names = element_names or {}
    weights = {int(c["entry"]): 1.0 + _number(c.get("live_sharpness", c.get("historical_score", 50))) / 100.0 for c in cohort}
    denominator = sum(weights.values()) or 1.0
    totals = defaultdict(lambda: {"in": 0.0, "out": 0.0, "managers_in": 0, "managers_out": 0})
    for raw_entry, rows in (transfers_by_entry or {}).items():
        entry = int(raw_entry)
        if entry not in weights:
            continue
        weight = weights[entry]
        seen_in, seen_out = set(), set()
        for row in rows or []:
            if int(row.get("event", -1)) != int(event):
                continue
            try:
                incoming, outgoing = int(row["element_in"]), int(row["element_out"])
            except (KeyError, TypeError, ValueError):
                continue
            if incoming not in seen_in:
                totals[incoming]["in"] += weight
                totals[incoming]["managers_in"] += 1
                seen_in.add(incoming)
            if outgoing not in seen_out:
                totals[outgoing]["out"] += weight
                totals[outgoing]["managers_out"] += 1
                seen_out.add(outgoing)
    result = []
    for element, row in totals.items():
        result.append({
            "element": element,
            "name": element_names.get(element, str(element)),
            "weighted_in_pct": round(100 * row["in"] / denominator, 1),
            "weighted_out_pct": round(100 * row["out"] / denominator, 1),
            "managers_in": row["managers_in"],
            "managers_out": row["managers_out"],
        })
    return sorted(result, key=lambda r: (-(r["weighted_in_pct"] - r["weighted_out_pct"]), -r["managers_in"], r["element"]))


def market_signals(elements, limit=20):
    """Price, availability, and transfer-momentum watchlist."""
    rows = []
    for player in elements or []:
        projection = player.get("price_change_projections")
        hourly = _number(player.get("price_change_hourly_rate"), 0)
        net = int(_number(player.get("transfers_in_event"), 0) - _number(player.get("transfers_out_event"), 0))
        chance = player.get("chance_of_playing_next_round")
        status = str(player.get("status") or "a")
        if not projection and not hourly and not net and status == "a":
            continue
        rows.append({
            "element": int(player["id"]),
            "name": player.get("web_name"),
            "now_cost": round(_number(player.get("now_cost")) / 10.0, 1),
            "projection": projection,
            "hourly_rate": hourly,
            "net_transfers_event": net,
            "status": status,
            "chance_next": chance,
            "locked_until": player.get("price_change_locked_until"),
            "calibrating": bool(player.get("price_change_calibrating")),
            "news": player.get("news") or None,
        })
    return sorted(rows, key=lambda r: (-abs(r["hourly_rate"]), -abs(r["net_transfers_event"]), r["element"]))[:limit]


def set_piece_signals(elements):
    """Return API-declared penalty/free-kick/corner hierarchy."""
    rows = []
    for player in elements or []:
        roles = {}
        for label, key in (("penalties", "penalties_order"), ("direct_freekicks", "direct_freekicks_order"), ("corners", "corners_and_indirect_freekicks_order")):
            order = player.get(key)
            if order is not None:
                try:
                    roles[label] = int(order)
                except (TypeError, ValueError):
                    pass
        if roles:
            rows.append({"element": int(player["id"]), "name": player.get("web_name"), "team": int(player["team"]), "roles": roles})
    return rows


def live_points(picks_payload, live_by_element):
    if not isinstance(picks_payload, dict):
        return None
    total = 0.0
    for pick in picks_payload.get("picks", []) or []:
        try:
            total += _number(live_by_element.get(int(pick["element"]), {}).get("total_points"), 0) * int(pick.get("multiplier", 0))
        except (KeyError, TypeError, ValueError):
            return None
    return round(total, 1)


def cohort_live_swing(our_picks, picks_by_entry, live_payload):
    """Current-event points versus trusted rivals; explicitly cohort-only."""
    live = {int(row["id"]): row.get("stats", {}) for row in (live_payload or {}).get("elements", []) if row.get("id")}
    ours = live_points(our_picks, live)
    if ours is None:
        return None
    rivals = []
    for entry, picks in (picks_by_entry or {}).items():
        points = live_points(picks, live)
        if points is not None:
            rivals.append({"entry": int(entry), "live_points": points, "swing_vs_us": round(points - ours, 1)})
    rivals.sort(key=lambda r: -r["live_points"])
    return {"our_live_points": ours, "rivals": rivals, "sample_size": len(rivals)}


def monthly_totals(rows, monthly_ledger, month_key, league_id, our_entry, prize):
    event_scores = ((monthly_ledger or {}).get(month_key, {}) or {}).get(str(league_id), {}) or {}
    scores = defaultdict(float)
    for event_payload in event_scores.values():
        if not isinstance(event_payload, dict):
            continue
        for entry, points in event_payload.items():
            scores[str(entry)] += _number(points)
    if str(our_entry) not in scores:
        return {"league_id": int(league_id), "month": month_key, "rank": None, "points": None, "gap_to_first": None, "prize": prize}
    ranked = sorted(((int(entry), _number(points)) for entry, points in scores.items()), key=lambda item: (-item[1], item[0]))
    our_index = next(i for i, item in enumerate(ranked) if item[0] == int(our_entry))
    our_points = ranked[our_index][1]
    return {
        "league_id": int(league_id), "month": month_key, "rank": our_index + 1,
        "points": round(our_points, 1), "gap_to_first": round(max(0.0, ranked[0][1] - our_points + (0 if our_index == 0 else 1)), 1),
        "prize": prize,
    }


def simulate_prize_probabilities(rows, our_entry, bands, remaining_gws, *, simulations=1000, sigma_per_gw=14.0, seed=1):
    """Deterministic neutral-edge Monte Carlo; estimates rank-band uncertainty.

    This is a risk view, not a player forecast. All managers receive the same
    future mean; independent score variance represents unresolved FPL outcomes.
    """
    ranked = [(int(r["entry"]), _number(r.get("total"))) for r in rows if r.get("rank") is not None and r.get("total") is not None]
    own = next((points for entry, points in ranked if entry == int(our_entry)), None)
    if own is None or len(ranked) < 2:
        return {"available": False, "reason": "Live standings unavailable", "simulations": 0}
    rng = random.Random(int(seed))
    spread = max(1.0, _number(sigma_per_gw, 14.0) * math.sqrt(max(1, int(remaining_gws))))
    ranks = []
    band_hits = defaultdict(int)
    for _ in range(max(100, int(simulations))):
        our_final = own + rng.gauss(0, spread)
        final_rank = 1 + sum(1 for entry, points in ranked if entry != int(our_entry) and points + rng.gauss(0, spread) > our_final)
        ranks.append(final_rank)
        for band in bands or []:
            if int(band["rank_from"]) <= final_rank <= int(band["rank_to"]):
                band_hits[str(band.get("prize"))] += 1
    count = len(ranks)
    return {
        "available": True,
        "simulations": count,
        "assumption": "neutral future scoring edge; independent uncertainty",
        "expected_rank": round(statistics.mean(ranks), 1),
        "p_top_1": round(100 * sum(r <= 1 for r in ranks) / count, 1),
        "p_top_10": round(100 * sum(r <= 10 for r in ranks) / count, 1),
        "p_top_40": round(100 * sum(r <= 40 for r in ranks) / count, 1),
        "band_probabilities": {key: round(100 * value / count, 1) for key, value in band_hits.items()},
    }
