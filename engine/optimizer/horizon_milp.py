"""Three-gameweek FPL state optimizer for the canonical V4.1 plan.

The model jointly chooses squad ownership, transfers, legal lineups, captaincy,
bank and free-transfer rollover.  Only the first gameweek is executable; later
weeks are conditional roadmap states and are rebuilt on every /simulate.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

import pulp


SQUAD_QUOTA = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
LINEUP_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
LINEUP_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}


def _mean(player: dict[str, Any], week: int) -> float:
    values = player.get("xpts_by_gw") or []
    return float(values[week] or 0.0) if week < len(values) else 0.0


def _variance(player: dict[str, Any], week: int) -> float:
    values = player.get("variance_by_gw") or []
    return max(0.0, float(values[week] or 0.0)) if week < len(values) else 0.0


def _robust(player: dict[str, Any], week: int, risk_penalty: float) -> float:
    return _mean(player, week) - risk_penalty * math.sqrt(_variance(player, week))


def _captain_utility(player: dict[str, Any], week: int) -> float:
    """Mirror V4's ceiling-aware captain score inside the MILP objective."""
    role_bonus = {"FWD": 0.15, "MID": 0.12, "DEF": 0.0, "GKP": -0.15}.get(
        player.get("position"), 0.0
    )
    return _mean(player, week) + 0.33 * math.sqrt(_variance(player, week)) + role_bonus


def _prune(current: list[dict[str, Any]], candidates: list[dict[str, Any]],
           horizon: int, per_position: int = 28) -> list[dict[str, Any]]:
    """Bound CBC size without discarding owned, high-value or cheap enablers."""
    by_id = {int(player["id"]): player for player in current + candidates}
    owned = {int(player["id"]) for player in current}
    keep = set(owned)
    for position in SQUAD_QUOTA:
        rows = [player for player in by_id.values() if player.get("position") == position]
        rows.sort(key=lambda p: -sum((1.0, 0.7, 0.5)[week] * _mean(p, week)
                                     for week in range(min(horizon, 3))))
        keep.update(int(player["id"]) for player in rows[:per_position])
        keep.update(int(player["id"]) for player in sorted(rows, key=lambda p: p["cost"])[:8])
    return [by_id[player_id] for player_id in sorted(keep)]


def _captain_eligible(player: dict[str, Any], min_start: float,
                      min_minutes: float) -> bool:
    return (float(player.get("p_start") or 0.0) >= min_start
            and float(player.get("expected_minutes") or 0.0) >= min_minutes)


def _formation(lineup: list[dict[str, Any]]) -> str:
    counts = Counter(player["position"] for player in lineup)
    return f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}"


def optimize_horizon(current_squad: list[dict[str, Any]],
                     candidates: list[dict[str, Any]], bank: int,
                     free_transfers: int, *, horizon: int = 3,
                     weights=(1.0, 0.7, 0.5), risk_penalty: float = 0.25,
                     bench_weight: float = 0.08, max_transfers_per_gw: int = 2,
                     max_paid_transfers: int = 1, paid_transfers_allowed: bool = True,
                     protected: set[int] | None = None,
                     excluded: set[int] | None = None,
                     captain_min_start: float = 0.75,
                     captain_min_minutes: float = 65.0,
                     transfer_friction: float = 0.15,
                     timeout_seconds: int = 25) -> dict[str, Any]:
    """Solve a legal receding-horizon FPL plan and return JSON-safe evidence."""
    if len(current_squad) != 15:
        raise ValueError("current squad must contain 15 players")
    horizon = max(1, min(int(horizon), 3))
    protected = {int(value) for value in (protected or set())}
    excluded = {int(value) for value in (excluded or set())}
    players = _prune(current_squad, candidates, horizon)
    by_id = {int(player["id"]): player for player in players}
    ids = sorted(by_id)
    owned = {int(player["id"]) for player in current_squad}
    if not owned.issubset(by_id):
        raise ValueError("candidate pruning removed an owned player")

    model = pulp.LpProblem("fpl_v41_horizon", pulp.LpMaximize)
    squad = pulp.LpVariable.dicts("squad", (ids, range(horizon)), cat="Binary")
    lineup = pulp.LpVariable.dicts("lineup", (ids, range(horizon)), cat="Binary")
    captain = pulp.LpVariable.dicts("captain", (ids, range(horizon)), cat="Binary")
    transfer_in = pulp.LpVariable.dicts("tin", (ids, range(horizon)), cat="Binary")
    transfer_out = pulp.LpVariable.dicts("tout", (ids, range(horizon)), cat="Binary")
    cash = pulp.LpVariable.dicts("bank", range(horizon), lowBound=0, cat="Integer")
    ft = pulp.LpVariable.dicts("ft", range(horizon + 1), lowBound=0, upBound=5, cat="Integer")
    free_used = pulp.LpVariable.dicts("free_used", range(horizon), lowBound=0, upBound=5, cat="Integer")
    hits = pulp.LpVariable.dicts("hits", range(horizon), lowBound=0, cat="Integer")
    lost_roll = pulp.LpVariable.dicts("lost_roll", range(horizon), lowBound=0, upBound=1, cat="Integer")
    transfer_count = {}

    model += ft[0] == max(0, min(5, int(free_transfers)))
    for week in range(horizon):
        transfer_count[week] = pulp.lpSum(transfer_in[player_id][week] for player_id in ids)
        model += transfer_count[week] == pulp.lpSum(transfer_out[player_id][week] for player_id in ids)
        model += transfer_count[week] <= max(0, int(max_transfers_per_gw))
        model += free_used[week] <= transfer_count[week]
        model += free_used[week] <= ft[week]
        model += hits[week] == transfer_count[week] - free_used[week]
        model += hits[week] <= (max(0, int(max_paid_transfers)) if paid_transfers_allowed else 0)
        model += ft[week + 1] == ft[week] - free_used[week] + 1 - lost_roll[week]
        model += lost_roll[week] >= ft[week] - free_used[week] - 4

        for player_id in ids:
            initial = 1 if player_id in owned else 0
            previous = initial if week == 0 else squad[player_id][week - 1]
            model += squad[player_id][week] == previous + transfer_in[player_id][week] - transfer_out[player_id][week]
            model += transfer_in[player_id][week] + transfer_out[player_id][week] <= 1
            model += lineup[player_id][week] <= squad[player_id][week]
            model += captain[player_id][week] <= lineup[player_id][week]
            if player_id in protected:
                model += transfer_out[player_id][week] == 0
            if player_id in excluded:
                model += transfer_in[player_id][week] == 0
                model += lineup[player_id][week] == 0
                model += captain[player_id][week] == 0

        model += pulp.lpSum(squad[player_id][week] for player_id in ids) == 15
        model += pulp.lpSum(lineup[player_id][week] for player_id in ids) == 11
        model += pulp.lpSum(captain[player_id][week] for player_id in ids) == 1
        for position, quota in SQUAD_QUOTA.items():
            pos_ids = [player_id for player_id in ids if by_id[player_id]["position"] == position]
            model += pulp.lpSum(squad[player_id][week] for player_id in pos_ids) == quota
            model += pulp.lpSum(lineup[player_id][week] for player_id in pos_ids) >= LINEUP_MIN[position]
            model += pulp.lpSum(lineup[player_id][week] for player_id in pos_ids) <= LINEUP_MAX[position]
        for club in sorted({by_id[player_id]["club"] for player_id in ids}):
            club_ids = [player_id for player_id in ids if by_id[player_id]["club"] == club]
            model += pulp.lpSum(squad[player_id][week] for player_id in club_ids) <= 3

        eligible = [player_id for player_id in ids
                    if _captain_eligible(by_id[player_id], captain_min_start, captain_min_minutes)]
        if not eligible:
            raise ValueError("no player clears the captain minutes gate")
        model += pulp.lpSum(captain[player_id][week] for player_id in eligible) == 1

        sell = pulp.lpSum(
            int(by_id[player_id].get("selling_price", by_id[player_id]["cost"]))
            * transfer_out[player_id][week] for player_id in ids
        )
        buy = pulp.lpSum(int(by_id[player_id]["cost"]) * transfer_in[player_id][week]
                         for player_id in ids)
        previous_cash = int(bank) if week == 0 else cash[week - 1]
        model += cash[week] == previous_cash + sell - buy

    objective = []
    for week in range(horizon):
        weight = float(weights[week])
        for player_id in ids:
            player = by_id[player_id]
            robust = _robust(player, week, risk_penalty)
            mean = _mean(player, week)
            objective.append(weight * robust * lineup[player_id][week])
            objective.append(weight * _captain_utility(player, week) * captain[player_id][week])
            objective.append(weight * bench_weight * robust
                             * (squad[player_id][week] - lineup[player_id][week]))
        objective.append(-weight * 4.0 * hits[week])
        objective.append(-transfer_friction * transfer_count[week])
        objective.append(-0.01 * lost_roll[week])
    objective.extend([0.01 * cash[horizon - 1], 0.05 * ft[horizon]])
    model += pulp.lpSum(objective)

    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=max(5, int(timeout_seconds)))
    status_code = model.solve(solver)
    status = pulp.LpStatus.get(status_code, str(status_code))
    if status not in {"Optimal", "Integer Feasible"}:
        raise RuntimeError(f"V4.1 horizon MILP failed: {status}")

    weeks = []
    for week in range(horizon):
        owned_week = [by_id[player_id] for player_id in ids
                      if pulp.value(squad[player_id][week]) > 0.5]
        lineup_week = [by_id[player_id] for player_id in ids
                       if pulp.value(lineup[player_id][week]) > 0.5]
        captain_week = next(by_id[player_id] for player_id in ids
                            if pulp.value(captain[player_id][week]) > 0.5)
        incoming = [player_id for player_id in ids if pulp.value(transfer_in[player_id][week]) > 0.5]
        outgoing = [player_id for player_id in ids if pulp.value(transfer_out[player_id][week]) > 0.5]
        outgoing_by_pos = {by_id[player_id]["position"]: [] for player_id in outgoing}
        for player_id in outgoing:
            outgoing_by_pos[by_id[player_id]["position"]].append(player_id)
        moves = []
        for player_id in incoming:
            position = by_id[player_id]["position"]
            out_id = outgoing_by_pos[position].pop(0)
            moves.append({
                "element_out": out_id, "element_in": player_id,
                "out_name": by_id[out_id]["name"], "in_name": by_id[player_id]["name"],
                "out_pos": position, "in_pos": position,
                "selling_price": int(by_id[out_id].get("selling_price", by_id[out_id]["cost"])),
                "purchase_price": int(by_id[player_id]["cost"]),
            })
        bench = [player for player in owned_week if player["id"] not in {p["id"] for p in lineup_week}]
        bench = (sorted((p for p in bench if p["position"] == "GKP"), key=lambda p: -_mean(p, week))
                 + sorted((p for p in bench if p["position"] != "GKP"), key=lambda p: -_mean(p, week)))
        xi_mean = sum(_mean(player, week) for player in lineup_week)
        xi_robust = sum(_robust(player, week, risk_penalty) for player in lineup_week)
        week_hits = int(round(pulp.value(hits[week]) or 0))
        weeks.append({
            "gw_offset": week, "formation": _formation(lineup_week),
            "transfers": moves, "transfer_count": len(moves), "hits": week_hits,
            "free_transfers_before": int(round(pulp.value(ft[week]) or 0)),
            "free_transfers_after": int(round(pulp.value(ft[week + 1]) or 0)),
            "bank_after": int(round(pulp.value(cash[week]) or 0)),
            "lineup_ids": [player["id"] for player in lineup_week],
            "bench_ids": [player["id"] for player in bench],
            "captain_id": captain_week["id"],
            "mean_points_with_captain": round(xi_mean + _mean(captain_week, week) - 4 * week_hits, 2),
            "robust_points_with_captain": round(xi_robust + _mean(captain_week, week) - 4 * week_hits, 2),
        })
    return {
        "optimizer": "horizon-milp", "optimizer_version": "v4.1",
        "status": status, "horizon": horizon,
        "objective": round(float(pulp.value(model.objective) or 0.0), 3),
        "candidate_pool_size": len(players), "weights": list(weights[:horizon]),
        "risk_penalty": risk_penalty, "bench_weight": bench_weight,
        "captain_gate": {"min_start": captain_min_start, "min_minutes": captain_min_minutes},
        "weeks": weeks,
    }
