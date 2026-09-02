"""Deterministic, read-only multi-transfer NET-EV research optimizer.

The optimizer evaluates snapshot-legal same-position replacements across a
weighted gameweek horizon. It includes hit cost and a configurable value for
preserving free transfers. It never executes FPL changes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations, product
from typing import Iterable

from .projection_types import PlayerProjection


OPTIMIZER_VERSION = "net-ev-multiweek-v1"


@dataclass(frozen=True)
class MultiWeekContext:
    bank: float
    free_transfers: int
    weights: tuple[float, ...]
    max_transfers: int = 2
    hit_cost: float = 4.0
    free_transfer_value: float = 1.5
    risk_weight: float = 0.05
    active_chip: str | None = None
    candidate_pool_per_position: int = 6
    # Official FPL availability is deliberately supplied by the caller.  The
    # projection model does not own injury/status truth and must not quietly
    # select a flagged player into a recommended squad.
    eligible_player_ids: frozenset[int] | None = None


def optimize_multiweek_transfers(
    squad_ids: Iterable[int],
    players: dict[int, PlayerProjection],
    prices: dict[int, float],
    horizon: list[dict[int, PlayerProjection]],
    context: MultiWeekContext,
    limit: int = 10,
) -> dict:
    squad = tuple(dict.fromkeys(int(element) for element in squad_ids))
    if len(squad) != 15 or any(element not in players for element in squad):
        return {"status": "invalid_squad", "optimizer_version": OPTIMIZER_VERSION, "plans": []}
    def weighted(element: int) -> float:
        return round(sum(
            context.weights[index] * week.get(element, players[element]).xpts_mean
            for index, week in enumerate(horizon[:len(context.weights)])
        ), 4)

    weighted_points = {element: weighted(element) for element in players}
    chip = (context.active_chip or "").lower().replace("_", "")
    if chip in {"wildcard", "freehit"}:
        return _optimize_chip_squad(squad, players, prices, weighted_points, context, chip)
    candidates_by_position: dict[str, list[int]] = {}
    squad_set = set(squad)
    for position in {player.position for player in players.values()}:
        candidates_by_position[position] = sorted(
            (element for element, player in players.items()
             if player.position == position and element not in squad_set
             and (context.eligible_player_ids is None or element in context.eligible_player_ids)),
            key=lambda element: weighted_points[element], reverse=True,
        )[:context.candidate_pool_per_position]

    plans = [{
        "transfers": [], "transfer_count": 0, "gross_horizon_gain": 0.0,
        "hit_cost": 0.0, "free_transfer_opportunity_cost": 0.0,
        "risk_penalty": 0.0, "net_ev": 0.0, "bank_after": round(context.bank, 1),
        "club_limit_ok": True, "budget_ok": True,
    }]
    maximum = max(0, min(context.max_transfers, 3, len(squad)))
    for transfer_count in range(1, maximum + 1):
        for outgoing_ids in combinations(squad, transfer_count):
            option_lists = [candidates_by_position.get(players[element].position, []) for element in outgoing_ids]
            if any(not options for options in option_lists):
                continue
            for incoming_ids in product(*option_lists):
                if len(set(incoming_ids)) != transfer_count:
                    continue
                new_squad = (squad_set - set(outgoing_ids)) | set(incoming_ids)
                clubs = Counter(players[element].team for element in new_squad)
                if any(count > 3 for count in clubs.values()):
                    continue
                spend = sum(prices.get(element, 0.0) for element in incoming_ids)
                sales = sum(prices.get(element, 0.0) for element in outgoing_ids)
                bank_after = context.bank + sales - spend
                if bank_after < -1e-9:
                    continue
                gross = sum(weighted_points[element] for element in incoming_ids) - sum(
                    weighted_points[element] for element in outgoing_ids
                )
                hits = max(0, transfer_count - max(0, context.free_transfers)) * context.hit_cost
                opportunity = min(transfer_count, max(0, context.free_transfers)) * context.free_transfer_value
                risk = sum(max(0.0, players[element].p90 - players[element].p10) for element in incoming_ids)
                risk -= sum(max(0.0, players[element].p90 - players[element].p10) for element in outgoing_ids)
                risk_penalty = max(0.0, risk) * context.risk_weight
                net_ev = gross - hits - opportunity - risk_penalty
                transfers = [{
                    "out_element": outgoing, "out_name": players[outgoing].name,
                    "in_element": incoming, "in_name": players[incoming].name,
                    "position": players[outgoing].position,
                    "weighted_gain": round(weighted_points[incoming] - weighted_points[outgoing], 2),
                } for outgoing, incoming in zip(outgoing_ids, incoming_ids)]
                plans.append({
                    "transfers": transfers, "transfer_count": transfer_count,
                    "gross_horizon_gain": round(gross, 2), "hit_cost": round(hits, 2),
                    "free_transfer_opportunity_cost": round(opportunity, 2),
                    "risk_penalty": round(risk_penalty, 2), "net_ev": round(net_ev, 2),
                    "bank_after": round(bank_after, 1), "club_limit_ok": True, "budget_ok": True,
                })
    ranked = sorted(plans, key=lambda plan: (plan["net_ev"], -plan["transfer_count"]), reverse=True)
    return {
        "status": "research_only", "optimizer_version": OPTIMIZER_VERSION,
        "active_chip": context.active_chip, "horizon_weights": list(context.weights),
        "hit_cost": context.hit_cost, "free_transfer_value": context.free_transfer_value,
        "evaluated_plans": len(plans), "plans": ranked[:max(1, limit)],
        "disclaimer": "Read-only NET-EV research; verify selling prices, free transfers and late team news in FPL.",
    }


def _optimize_chip_squad(
    current_squad: tuple[int, ...],
    players: dict[int, PlayerProjection],
    prices: dict[int, float],
    weighted_points: dict[int, float],
    context: MultiWeekContext,
    chip: str,
) -> dict:
    """Beam-search a legal 15-player wildcard/free-hit squad and legal XI."""
    requirements = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    candidates = {
        position: sorted(
            (element for element, player in players.items()
             if player.position == position
             and (context.eligible_player_ids is None or element in context.eligible_player_ids)),
            key=lambda element: weighted_points[element], reverse=True,
        )[:40]
        for position in ("GKP", "DEF", "MID", "FWD")
    }
    budget = sum(prices.get(element, 0.0) for element in current_squad) + context.bank
    states: list[tuple[tuple[int, ...], float, float, Counter[str]]] = [((), 0.0, 0.0, Counter())]
    for slot, position in enumerate(requirements):
        remaining = requirements[slot + 1:]
        minimum_remaining = sum(
            min((prices.get(element, 100.0) for element in candidates[next_position]), default=100.0)
            for next_position in remaining
        )
        expanded = []
        for chosen, cost, score_value, clubs in states:
            for element in candidates[position]:
                if element in chosen or clubs[players[element].team] >= 3:
                    continue
                new_cost = cost + prices.get(element, 0.0)
                if new_cost + minimum_remaining > budget + 1e-9:
                    continue
                new_clubs = clubs.copy(); new_clubs[players[element].team] += 1
                expanded.append((chosen + (element,), new_cost, score_value + weighted_points[element], new_clubs))
        states = sorted(expanded, key=lambda state: state[2], reverse=True)[:3000]
        if not states:
            return {"status": "no_legal_chip_squad", "optimizer_version": OPTIMIZER_VERSION,
                    "active_chip": context.active_chip, "plans": []}
    chosen, cost, total, _ = states[0]
    by_position = {
        position: sorted((element for element in chosen if players[element].position == position),
                         key=lambda element: weighted_points[element], reverse=True)
        for position in ("GKP", "DEF", "MID", "FWD")
    }
    lineup_options = []
    for defenders in range(3, 6):
        for midfielders in range(2, 6):
            forwards = 10 - defenders - midfielders
            if 1 <= forwards <= 3:
                lineup = (by_position["GKP"][:1] + by_position["DEF"][:defenders]
                          + by_position["MID"][:midfielders] + by_position["FWD"][:forwards])
                lineup_options.append((sum(weighted_points[element] for element in lineup), lineup))
    lineup_score, lineup = max(lineup_options, key=lambda option: option[0])
    captain = max(lineup, key=lambda element: weighted_points[element])
    incoming = set(chosen) - set(current_squad)
    outgoing = set(current_squad) - set(chosen)
    return {
        "status": "research_only", "optimizer_version": OPTIMIZER_VERSION,
        "active_chip": context.active_chip, "chip_mode": "temporary" if chip == "freehit" else "permanent",
        "horizon_weights": list(context.weights), "evaluated_plans": len(states),
        "plans": [{
            "plan_type": "full_squad_chip", "squad": list(chosen), "lineup": lineup,
            "bench": [element for element in chosen if element not in lineup], "captain": captain,
            "incoming": sorted(incoming), "outgoing": sorted(outgoing),
            "transfer_count": len(incoming), "hit_cost": 0.0,
            "weighted_squad_points": round(total, 2), "weighted_lineup_points": round(lineup_score, 2),
            "budget": round(budget, 1), "cost": round(cost, 1), "bank_after": round(budget - cost, 1),
            "club_limit_ok": True, "budget_ok": True,
        }],
        "disclaimer": "Read-only chip research; verify current selling prices and FPL chip availability.",
    }
