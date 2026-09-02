"""Beat-them engine: differential targets vs sharp opponents.

Given a target opponent's current squad (element ids), rank all selectable
players by xPts (bonus-adjusted, from the live predictions snapshot) and flag
those the opponent LACKS — the differential picks that can claw back rank when
we're behind, or the "keep away from them" reads when we're ahead.

Also computes captaincy differentiation: if the sharp opponent captains X and
we're behind, the value of NOT matching X increases (higher variance needed).

Data: data/processed/predictions_gw1.json (566 players with bonus-adjusted xPts)
      + opponent picks from league_monitor snapshot.
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

POS_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def load_predictions():
    try:
        with open(os.path.join(BASE, "data", "processed", "predictions_gw1.json"), encoding="utf-8") as f:
            return json.load(f).get("players", [])
    except (OSError, ValueError, TypeError):
        return []


def load_bootstrap():
    with open(os.path.join(BASE, "data", "raw", "bootstrap-static.json"), encoding="utf-8") as f:
        return json.load(f)


def opponent_squad_ids(monitor_snapshot, opponent_entry):
    """Element ids in the opponent's current GW picks (from monitor snapshot)."""
    entry = (monitor_snapshot or {}).get("entries", {}).get(str(opponent_entry), {})
    picks = entry.get("picks") or []
    return [p.get("element") for p in picks if p.get("element")]


def differential_targets(opponent_entry, monitor_snapshot=None, top_n=10,
                         position=None, max_cost=None):
    """High-xPts players the opponent does NOT have.

    Returns list of {id, name, position, cost, xpts, xpts_horizon}.
    """
    boot = load_bootstrap()
    names = {e["id"]: e["web_name"] for e in boot["elements"]}
    teams = {t["id"]: t["short_name"] for t in boot["teams"]}
    preds = load_predictions()
    opp_ids = set(opponent_squad_ids(monitor_snapshot, opponent_entry))

    cands = []
    for p in preds:
        if p["id"] in opp_ids:
            continue
        el = next((e for e in boot["elements"] if e["id"] == p["id"]), None)
        if not el:
            continue
        pos = POS_MAP.get(el["element_type"], "MID")
        if position and pos != position:
            continue
        cost = int(el["now_cost"])
        if max_cost and cost > max_cost * 10:
            continue
        cands.append({
            "id": p["id"], "name": names.get(p["id"], "?"),
            "position": pos, "club": teams.get(el["team"], "?"),
            "cost": cost / 10.0, "xpts": round(float(p.get("xpts", 0)), 2),
        })
    cands.sort(key=lambda c: -c["xpts"])
    return cands[:top_n]


def captain_differentiation(opponent_captain_id, our_captain_id, preds=None):
    """If the opponent captains a player we don't, note the differentiation."""
    preds = preds or load_predictions()
    by_id = {p["id"]: p for p in preds}
    note = None
    if opponent_captain_id and opponent_captain_id != our_captain_id:
        opp = by_id.get(opponent_captain_id, {})
        note = {
            "opponent_captain": opp.get("name", "?"),
            "opponent_captain_xpts": round(float(opp.get("xpts", 0)), 2),
            "suggestion": "Different captain = variance. If behind, this is your swing lever; if ahead, consider matching to protect."
        }
    return note
