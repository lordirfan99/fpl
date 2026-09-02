"""Beam-search multi-GW transfer planner.

This is a planning layer, not a replacement for the execution solver. It scores
legal transfer sequences over several GWs, values banked free transfers, and
penalizes hits/uncertainty. The best first action can be surfaced to the weekly
pipeline while preserving a human approval gate.
"""
from copy import deepcopy


def _club_counts(squad):
    out = {}
    for p in squad:
        out[p["club"]] = out.get(p["club"], 0) + 1
    return out


def _gw_score(squad, gw_index, risk_penalty=0.15):
    score = 0.0
    for p in squad:
        arr = p.get("xpts_by_gw") or []
        var = p.get("variance_by_gw") or []
        xp = arr[gw_index] if gw_index < len(arr) else 0.0
        vv = var[gw_index] if gw_index < len(var) else 0.0
        score += float(xp) - risk_penalty * (float(vv) ** 0.5)
    return score


def _moves(squad, candidates, bank, club_max=3, limit=28):
    ids = {p["id"] for p in squad}
    clubs = _club_counts(squad)
    moves = []
    for out in squad:
        sell = out.get("selling_price", out["cost"])
        for inc in candidates:
            if inc["id"] in ids or inc["position"] != out["position"]:
                continue
            if inc["cost"] - sell > bank:
                continue
            if clubs.get(inc["club"], 0) >= club_max and inc["club"] != out["club"]:
                continue
            gain = sum(inc.get("xpts_by_gw", [])) - sum(out.get("xpts_by_gw", []))
            moves.append((gain, out, inc))
    moves.sort(key=lambda x: -x[0])
    return moves[:limit]


def plan_sequences(current_squad, candidates, bank, free_transfers, horizon=4,
                   beam_width=18, hit_cost=4.0, ft_roll_value=0.35,
                   risk_penalty=0.15):
    """Return best sequence and alternatives using compact beam search.

    State is squad/bank/FT. Each GW explores roll plus top legal single moves;
    hits are allowed but charged. This intentionally limits branching so it can
    run inside the pre-deadline job without becoming an optimization bottleneck.
    """
    initial = {
        "squad": deepcopy(current_squad), "bank": bank,
        "ft": max(0, int(free_transfers)), "score": 0.0, "actions": []}
    beam = [initial]
    for g in range(horizon):
        nxt = []
        for st in beam:
            # Roll transfer.
            rolled = deepcopy(st)
            rolled["score"] += _gw_score(rolled["squad"], g, risk_penalty)
            rolled["ft"] = min(5, rolled["ft"] + 1)
            rolled["score"] += ft_roll_value * rolled["ft"]
            rolled["actions"].append({"gw_offset": g, "action": "roll"})
            nxt.append(rolled)

            for _, out, inc in _moves(st["squad"], candidates, st["bank"]):
                ns = deepcopy(st)
                use_hit = ns["ft"] <= 0
                if use_hit:
                    ns["score"] -= hit_cost
                else:
                    ns["ft"] -= 1
                sell = out.get("selling_price", out["cost"])
                ns["bank"] += sell - inc["cost"]
                incoming = deepcopy(inc)
                incoming["selling_price"] = incoming["cost"]
                ns["squad"] = [incoming if p["id"] == out["id"] else p for p in ns["squad"]]
                ns["score"] += _gw_score(ns["squad"], g, risk_penalty)
                ns["actions"].append({
                    "gw_offset": g, "action": "transfer", "hit": use_hit,
                    "element_out": out["id"], "element_in": inc["id"],
                    "out_name": out["name"], "in_name": inc["name"]})
                nxt.append(ns)
        nxt.sort(key=lambda s: -s["score"])
        beam = nxt[:beam_width]
    best = beam[0] if beam else initial
    return {
        "score": round(best["score"], 2),
        "actions": best["actions"],
        "first_action": best["actions"][0] if best["actions"] else {"action": "roll"},
        "alternatives": [{"score": round(s["score"], 2), "actions": s["actions"]}
                         for s in beam[1:4]],
    }
