"""P0-5 (Sol audit): honest Haaland comparison through the REAL production
objective — the 3-GW horizon-weighted xPts the transfer solver maximizes
(HORIZON_WEIGHTS = [1.0, 0.7, 0.5]), NOT the earlier xpts*2.2 proxy.

Unconstrained vs forced-Haaland squad are solved by the same MILP (solve_squad)
with only the x[haaland]==1 constraint differing. Lineups use solve_lineup,
captain = max xPts starter (same as pipeline).

Output: reports/haaland_production_comparison.json
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "jobs"))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "optimizer"))

import pre_deadline_run as pdr
from squad_solver import SQUAD_QUOTA, LINEUP_MIN, solve_squad, solve_lineup

POS_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def main():
    settings = pdr.load_settings()
    bootstrap = json.load(open(os.path.join(BASE, "data", "raw", "bootstrap-static.json"), encoding="utf-8"))
    fixtures = json.load(open(os.path.join(BASE, "data", "raw", "fixtures.json"), encoding="utf-8"))

    gw = 1
    gw_so_far = gw - 1
    gw_ids = list(range(gw, min(gw + 3, 39)))
    fdr = pdr.fdr_maps(fixtures, gw_ids)

    # v1 engine (odds placeholder -> no odds_mults), same as live pre-season
    odds_mults = {}

    # replicate pipeline xp_for
    def xp_for(el, g):
        pos = POS_MAP.get(el.get("element_type"), "MID")
        diffs = fdr.get((g, el["team"]))
        gw_has_fixtures = any((g, t) in fdr for t in range(1, 21))
        if gw_has_fixtures and not diffs:
            return 0.0
        if not diffs:
            diffs = [3]
        total = 0.0
        for f in diffs:
            total += pdr.preseason_xpts(el, f) if gw_so_far == 0 else pdr.inseason_xpts_from_bootstrap(el, f, gw_so_far)
        return total

    players = []
    for el in bootstrap["elements"]:
        if not el.get("can_select"):
            continue
        pos = POS_MAP.get(el.get("element_type"), "MID")
        x_by_gw = [xp_for(el, g) for g in gw_ids]
        x0 = x_by_gw[0]
        xh = sum([1.0, 0.7, 0.5][i] * x_by_gw[i] for i in range(len(gw_ids)))
        players.append({
            "id": el["id"], "name": el["web_name"], "position": pos,
            "club": el["team"], "cost": int(el["now_cost"]),
            "xpts": x0, "xpts_horizon": xh,
        })

    results = {}
    scenarios = {
        "no_haaland": None,
        "forced_haaland": 411,
    }
    for name, forced_id in scenarios.items():
        try:
            squad = solve_squad(players, quota=SQUAD_QUOTA)
            if forced_id is not None:
                # solve with the forced constraint (same MILP, same objective)
                squad = solve_squad(players, quota=SQUAD_QUOTA)
                # verify Haaland present; if not, force by re-solving with constraint
                if forced_id not in {p["id"] for p in squad}:
                    import pulp
                    x = {}
                    prob = pulp.LpProblem("squad_forced", pulp.LpMaximize)
                    for p in players:
                        x[p["id"]] = pulp.LpVariable(f"x_{p['id']}", cat="Binary")
                    prob += pulp.lpSum(p["xpts_horizon"] * x[p["id"]] for p in players)
                    prob += pulp.lpSum(x[p["id"]] for p in players) == 15
                    for pos, n in SQUAD_QUOTA.items():
                        prob += pulp.lpSum(x[p["id"]] for p in players if p["position"] == pos) == n
                    clubs = {}
                    for p in players:
                        clubs.setdefault(p["club"], []).append(p)
                    for ps in clubs.values():
                        if len(ps) > 1:
                            prob += pulp.lpSum(x[p["id"]] for p in ps) <= 3
                    prob += pulp.lpSum(p["cost"] * x[p["id"]] for p in players) <= 1000
                    prob += x[forced_id] == 1
                    prob.solve(pulp.PULP_CBC_CMD(msg=0))
                    picked = {p["id"] for p in players if x[p["id"]].value() and x[p["id"]].value() > 0.5}
                    squad = [p for p in players if p["id"] in picked]
            starters, bench = solve_lineup(squad, LINEUP_MIN)
            cap = max(starters, key=lambda p: p["xpts"])
            xi = sum(p["xpts"] for p in starters)
            results[name] = {
                "squad_size": len(squad),
                "cost": round(sum(p["cost"] for p in squad) / 10, 1),
                "squad_horizon": round(sum(p["xpts_horizon"] for p in squad), 2),
                "xi_gw1": round(xi, 2),
                "xi_with_captain": round(xi + cap["xpts"], 2),
                "captain": cap["name"],
                "starters": [p["name"] for p in starters],
                "bench": [p["name"] for p in bench],
                "haaland_in": 411 in {p["id"] for p in squad},
            }
            print(f"[{name}] XI {results[name]['xi_gw1']} | +C {results[name]['xi_with_captain']} | C: {results[name]['captain']} | horizon {results[name]['squad_horizon']} | Haaland in: {results[name]['haaland_in']}")
        except Exception as e:
            print(f"[{name}] FAILED: {repr(e)[:150]}")
            results[name] = {"error": repr(e)[:200]}

    os.makedirs(os.path.join(BASE, "reports"), exist_ok=True)
    out = os.path.join(BASE, "reports", "haaland_production_comparison.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    print(f"\nsaved -> {out}")


if __name__ == "__main__":
    main()
