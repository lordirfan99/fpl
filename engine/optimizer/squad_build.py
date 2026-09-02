"""
FPL Autopilot - pre-season GW1 squad build.

Pulls live bootstrap-static + fixtures, computes pre-season xPts for every
player (ppg-based rate with position-baseline floor, minutes-based play
probability, opponent FDR), then runs the MILP solver for the best 15 + XI.

Pre-season note: FPL resets `form` to 0.0, so we use last season's
points_per_game and minutes as the foundation, with ep_next and ownership
available as context. In-season the xPts model switches to the validated
rolling-form version.

Run: .venv/Scripts/python.exe optimizer/squad_build.py
"""
import json
import os
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "optimizer"))
sys.path.insert(0, os.path.join(BASE, "model"))

from squad_solver import solve_squad, solve_lineup
from xpts_model import position_baseline, fdr_multiplier

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
POS_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
MAX_MINUTES = 3420.0  # 38 GWs x 90 min


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def fnum(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def preseason_xpts(el, fdr):
    """Pre-season expected points proxy for GW1.
    Injury-aware: status 'i' or chance_of_playing < 50 -> zero (excluded by
    the solver). News text or 50-75 cop -> heavy penalty. This was the
    Garner bug: the model ignored status/news/cop entirely."""
    status = el.get("status", "a")
    cop = el.get("chance_of_playing_next_round")
    news = (el.get("news") or "").strip()

    if status == "i" or (cop is not None and cop < 50):
        return 0.0
    ppg = fnum(el.get("points_per_game"))
    minutes = fnum(el.get("minutes"))
    position = POS_MAP.get(el.get("element_type"), "MID")
    mp = min(0.9, 0.3 + 0.6 * (minutes / MAX_MINUTES))
    # injury soft-penalty: 50-75 cop or any news -> halve expected minutes
    if (cop is not None and cop < 75) or news:
        mp *= 0.5
    rate = max(ppg, position_baseline(position))
    return rate * mp * fdr_multiplier(fdr, position)


def build_player_list(bootstrap, fdr_gw1):
    players = []
    for el in bootstrap["elements"]:
        if not el.get("can_select"):
            continue
        players.append({
            "id": el["id"],
            "name": el["web_name"],
            "position": POS_MAP.get(el["element_type"], "MID"),
            "club": el["team"],
            "cost": int(el["now_cost"]),
            "xpts": preseason_xpts(el, fdr_gw1.get(el["team"], 3)),
            "ep_next": fnum(el.get("ep_next")),
            "selected": fnum(el.get("selected_by_percent")),
            "ppg": fnum(el.get("points_per_game")),
        })
    return players


def format_squad(players, title):
    lines = [title, "-" * 58]
    total = 0
    pos_order = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    for p in sorted(players, key=lambda x: (pos_order[x["position"]], -x["xpts"])):
        total += p["xpts"]
        lines.append("  %-4s %-16s club %-2d cost %5.1f  xPts %5.1f  ep_next %4.1f  sel %5.1f%%" % (
            p["position"], p["name"], p["club"], p["cost"] / 10, p["xpts"],
            p["ep_next"], p["selected"]))
    lines.append("-" * 58)
    lines.append(f"  TOTAL projected xPts: {total:.1f}  |  Cost: {sum(p['cost'] for p in players) / 10:.1f}m")
    return "\n".join(lines)


def main():
    bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    fixtures = fetch("https://fantasy.premierleague.com/api/fixtures/")

    # FDR per team for GW1
    fdr_gw1 = {}
    for f in fixtures:
        if f.get("event") == 1:
            fdr_gw1[f["team_h"]] = f["team_h_difficulty"]
            fdr_gw1[f["team_a"]] = f["team_a_difficulty"]

    players = build_player_list(bootstrap, fdr_gw1)
    print(f"players considered: {len(players)} | GW1 FDR map: {len(fdr_gw1)} teams")

    squad = solve_squad(players, budget=1000)
    starters, bench = solve_lineup(squad)

    print()
    print(format_squad(squad, "OPTIMIZED 15 (GW1):"))
    print()
    print(format_squad(starters, "STARTING XI:"))
    print()
    print(format_squad(bench, "BENCH (autosub order):"))

    # captain candidate = highest xPts in XI
    cap = max(starters, key=lambda p: p["xpts"])
    print()
    print(f"CAPTAIN candidate: {cap['name']} ({cap['position']}, club {cap['club']}) xPts {cap['xpts']:.1f}")

    # comparison vs current live squad
    print()
    print("COMPARISON vs current squad (entry 2797967):")
    try:
        sys.path.insert(0, os.path.join(BASE, "execution"))
        from fpl_client import FPLClient
        client = FPLClient()
        team = client.my_team(2797967)
        cur_ids = {p["element"] for p in team.get("picks", [])}
        cur = [p for p in players if p["id"] in cur_ids]
        if cur:
            print(format_squad(cur, "CURRENT SQUAD:"))
            new_total = sum(p["xpts"] for p in squad)
            cur_total = sum(p["xpts"] for p in cur)
            print(f"  -> Optimized {new_total:.1f} vs Current {cur_total:.1f} (delta {new_total - cur_total:+.1f} xPts GW1)")
    except Exception as e:
        print("  (current squad comparison skipped:", repr(e)[:120], ")")

    # save result (including current squad for delta reporting)
    os.makedirs(os.path.join(BASE, "data", "processed"), exist_ok=True)
    try:
        sys.path.insert(0, os.path.join(BASE, "execution"))
        from fpl_client import FPLClient
        client = FPLClient()
        team = client.my_team(2797967)
        cur_ids = {p["element"] for p in team.get("picks", [])}
        cur_squad = [p for p in players if p["id"] in cur_ids]
    except Exception:
        cur_squad = []
    with open(os.path.join(BASE, "data", "processed", "squad_build_gw1.json"), "w") as f:
        json.dump({"squad": squad, "starters": starters, "bench": bench,
                   "current_squad": cur_squad}, f, indent=1, default=str)
    print("\nsaved data/processed/squad_build_gw1.json")


if __name__ == "__main__":
    main()
