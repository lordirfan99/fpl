"""
FPL Autopilot - squad/lineup solver (MILP via PuLP/CBC).

Two problems:
  solve_squad  : best 15 players under FPL constraints (budget, position
                 quotas 2/5/5/3, max 3 per club). It jointly chooses the
                 starting XI + captain, scores the bench at only
                 ``BENCH_WEIGHT`` of face value (bench only scores via autosub),
                 and hard-caps the three outfield subs at ``OUTFIELD_BENCH_MAX``
                 total price. Together these stop the solver parking spare
                 budget on a premium player it would then bench (e.g. a £9.0m
                 striker as the 3rd forward in a 5-4-1). It still returns all
                 15; callers run solve_lineup to split XI/bench.
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
# Expected fraction of face-value xPts a benched player actually returns over a
# season (autosub only, and usually < 90 mins when it happens). Small but not
# zero, so the bench isn't filled with literally pointless £4.0m fodder.
BENCH_WEIGHT = 0.15
# Hard cap on the combined price of the three OUTFIELD substitutes (0.1m units).
# The objective still can't reward banked cash, so without this a cheap-XI build
# with a big surplus parks that money in the best affordable bench filler
# (a £9.0m striker sat on the bench). £15.0m still allows real depth
# (~£6m + £4.5m + £4.5m) while blocking that.
OUTFIELD_BENCH_MAX = 150


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


def solve_squad(players, budget=1000, quota=SQUAD_QUOTA, club_max=CLUB_MAX,
                bench_weight=BENCH_WEIGHT, lineup_min=LINEUP_MIN, lineup_max=LINEUP_MAX,
                outfield_bench_max=OUTFIELD_BENCH_MAX):
    """Pick the 15 that maximise starting-XI xPts + captain, with the bench
    scored at ``bench_weight`` of face value and the three outfield substitutes
    capped at ``outfield_bench_max`` total price. Returns the 15 (unchanged
    contract); run solve_lineup on the result for the XI/bench split.
    """
    prob = pulp.LpProblem("squad", pulp.LpMaximize)
    ids = [p["id"] for p in players]
    by_id = {p["id"]: p for p in players}
    in_squad = {i: pulp.LpVariable(f"x_{i}", cat="Binary") for i in ids}
    starts = {i: pulp.LpVariable(f"s_{i}", cat="Binary") for i in ids}
    captain = {i: pulp.LpVariable(f"c_{i}", cat="Binary") for i in ids}

    xp = {i: float(by_id[i].get("xpts", 0.0)) for i in ids}
    # starters at face value + captain again (2x total) + bench discounted
    prob += (
        pulp.lpSum(xp[i] * starts[i] for i in ids)
        + pulp.lpSum(xp[i] * captain[i] for i in ids)
        + bench_weight * pulp.lpSum(xp[i] * (in_squad[i] - starts[i]) for i in ids)
    )

    for i in ids:
        prob += starts[i] <= in_squad[i]
        prob += captain[i] <= starts[i]
    prob += pulp.lpSum(captain[i] for i in ids) == 1

    # --- 15-man squad shape ---
    prob += pulp.lpSum(in_squad[i] for i in ids) == 15
    for pos, n in quota.items():
        prob += pulp.lpSum(in_squad[i] for i in ids if by_id[i]["position"] == pos) == n
    clubs = {}
    for p in players:
        clubs.setdefault(p["club"], []).append(p["id"])
    for members in clubs.values():
        if len(members) > 1:
            prob += pulp.lpSum(in_squad[i] for i in members) <= club_max
    prob += pulp.lpSum(by_id[i]["cost"] * in_squad[i] for i in ids) <= budget

    # --- legal starting XI must exist inside the 15 ---
    prob += pulp.lpSum(starts[i] for i in ids) == 11
    for pos, n in lineup_min.items():
        prob += pulp.lpSum(starts[i] for i in ids if by_id[i]["position"] == pos) >= n
    for pos, n in lineup_max.items():
        prob += pulp.lpSum(starts[i] for i in ids if by_id[i]["position"] == pos) <= n

    # --- the 3 outfield subs must be cheap: no parking surplus on a bench body ---
    prob += pulp.lpSum(by_id[i]["cost"] * (in_squad[i] - starts[i])
                       for i in ids if by_id[i]["position"] != "GKP") <= outfield_bench_max

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"solver not optimal: {pulp.LpStatus[prob.status]}")
    return [by_id[i] for i in ids if in_squad[i].value() and in_squad[i].value() > 0.5]


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
