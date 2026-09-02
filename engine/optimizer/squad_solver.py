"""
FPL Autopilot - squad/lineup solver (MILP via PuLP/CBC).

Two problems:
  solve_squad  : best 15 players under FPL constraints (budget, position
                 quotas 2/5/5/3, max 3 per club). Objective: max sum xPts.
  solve_lineup : best starting XI from a fixed 15 (min 1 GK / 3 DEF / 2 MID /
                 1 FWD), then bench ordered by xPts (autosub order matters).

Players are dicts: {id, name, position (GKP/DEF/MID/FWD), cost (in 0.1m units
e.g. 1000 = 100.0m), club (int team id), xpts (float)}.
"""
import pulp

POSITIONS = ("GKP", "DEF", "MID", "FWD")
SQUAD_QUOTA = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
LINEUP_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
LINEUP_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}
CLUB_MAX = 3


def _solve(players, objective, constraints, name="prob"):
    prob = pulp.LpProblem(name, pulp.LpMaximize)
    x = {p["id"]: pulp.LpVariable(f"x_{p['id']}", cat="Binary") for p in players}
    prob += objective(x)
    for c in constraints:
        c(prob, x)
    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"solver not optimal: {pulp.LpStatus[prob.status]}")
    return {p["id"]: p for p in players if x[p["id"]].value() and x[p["id"]].value() > 0.5}


def solve_squad(players, budget=1000, quota=SQUAD_QUOTA, club_max=CLUB_MAX):
    def objective(x):
        return pulp.lpSum(p["xpts"] * x[p["id"]] for p in players)

    def constraints(prob, x):
        prob += pulp.lpSum(x[p["id"]] for p in players) == 15
        for pos, n in quota.items():
            prob += pulp.lpSum(x[p["id"]] for p in players if p["position"] == pos) == n
        clubs = {}
        for p in players:
            clubs.setdefault(p["club"], []).append(p)
        for ps in clubs.values():
            if len(ps) > 1:
                prob += pulp.lpSum(x[p["id"]] for p in ps) <= club_max
        prob += pulp.lpSum(p["cost"] * x[p["id"]] for p in players) <= budget

    picked = _solve(players, objective, [constraints], "squad")
    return [p for p in players if p["id"] in picked]


def solve_lineup(players, line_min=LINEUP_MIN, line_max=LINEUP_MAX):
    def objective(x):
        return pulp.lpSum(p["xpts"] * x[p["id"]] for p in players)

    def constraints(prob, x):
        prob += pulp.lpSum(x[p["id"]] for p in players) == 11
        for pos, n in line_min.items():
            prob += pulp.lpSum(x[p["id"]] for p in players if p["position"] == pos) >= n
        for pos, n in line_max.items():
            prob += pulp.lpSum(x[p["id"]] for p in players if p["position"] == pos) <= \
                    n

    picked = _solve(players, objective, [constraints], "lineup")
    starters = [p for p in players if p["id"] in picked]
    reserves = [p for p in players if p["id"] not in picked]
    # FPL pick position 12 is the reserve goalkeeper.  Preserve the optimizer's
    # xPts priority among the three outfield substitutes after that fixed slot.
    bench = (
        sorted((p for p in reserves if p["position"] == "GKP"), key=lambda p: -p["xpts"])
        + sorted((p for p in reserves if p["position"] != "GKP"), key=lambda p: -p["xpts"])
    )
    return starters, bench
