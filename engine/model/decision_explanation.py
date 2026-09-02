"""Build one user-facing explanation from the canonical V4 plan inputs.

This module does not choose or execute a move.  It exposes the reasoning that
the transfer and lineup solvers already used so Telegram and the dashboard can
render the same decision contract.
"""
from __future__ import annotations

import math
import datetime
from typing import Any

from transfer_solver import solve_transfers, squad_horizon_breakdown


def formation(starters: list[dict[str, Any]]) -> str:
    counts = {
        position: sum(1 for player in starters if player.get("position") == position)
        for position in ("DEF", "MID", "FWD")
    }
    return f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}"


def _route(transfers: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not transfers:
        return None
    gain = transfers[0].get("package_gain")
    if gain is None:
        gain = sum(float(move.get("gain", 0) or 0) for move in transfers)
    return {
        "moves": [
            {
                "out": move.get("out_name"),
                "in": move.get("in_name"),
                "hit": bool(move.get("hit")),
            }
            for move in transfers
        ],
        "horizon_gain": round(float(gain or 0), 1),
    }


def _shift_projection(players, offset):
    if offset <= 0:
        return players
    shifted = []
    for player in players:
        row = dict(player)
        means = list(player.get("xpts_by_gw") or [])[offset:]
        variances = list(player.get("variance_by_gw") or [])[offset:]
        row["xpts_by_gw"] = means
        row["variance_by_gw"] = variances
        if means:
            row["xpts"] = means[0]
            row["xpts_horizon"] = sum(
                weight * mean for weight, mean in zip((1.0, 0.7, 0.5), means)
            )
        shifted.append(row)
    return shifted


def _hypothetical_route(squad, candidates, bank, free_transfers, *,
                        max_joint_transfers, risk_penalty, bench_weight,
                        horizon_offset=0):
    shifted_squad = _shift_projection(squad, horizon_offset)
    shifted_candidates = _shift_projection(candidates, horizon_offset)
    transfers, _, _, _ = solve_transfers(
        shifted_squad, shifted_candidates, free_transfers, bank,
        hit_threshold=float("inf"), min_gain=0.1,
        max_joint_transfers=max_joint_transfers,
        risk_penalty=risk_penalty, bench_weight=bench_weight,
        max_paid_transfers=0,
    )
    return _route(transfers)


def _best_paid_candidate(squad, candidates, bank, *, risk_penalty, bench_weight):
    # Diagnostic only: lower the solver threshold to discover the best legal
    # paid move.  The live planner's actual paid-transfer gate remains intact.
    transfers, _, _, _ = solve_transfers(
        squad, candidates, 0, bank,
        hit_threshold=0.0, min_gain=0.0,
        max_joint_transfers=1,
        risk_penalty=risk_penalty, bench_weight=bench_weight,
        max_paid_transfers=1,
    )
    route = _route(transfers)
    if route:
        route["net_after_hit"] = round(route["horizon_gain"] - 4.0, 1)
    return route


def _template_comparison(plan, squad, bank):
    competitive = plan.get("competitive") or {}
    template = competitive.get("elite_template") or []
    owned = {int(player["id"]): player for player in squad if player.get("id") is not None}
    template_ids = {
        int(player["element"]) for player in template if player.get("element") is not None
    }
    owned_template = [
        player for player in template
        if player.get("element") is not None and int(player.get("element")) in owned
    ]
    missing = []
    for target in template:
        element = target.get("element")
        if element is None or int(element) in owned:
            continue
        position = target.get("position")
        same_position = [
            player for player in squad
            if player.get("position") == position and int(player.get("id", -1)) not in template_ids
        ]
        max_funds = max(
            (int(player.get("selling_price", player.get("cost", 0))) + int(bank)
             for player in same_position),
            default=int(bank),
        )
        cost = target.get("cost")
        # Competitive snapshots publish prices in £m (for example 7.5), while
        # the optimizer/account payload uses integer tenths (75).
        target_cost_tenths = None if cost is None else int(round(float(cost) * 10))
        cash_affordable = (
            None if target_cost_tenths is None else target_cost_tenths <= max_funds
        )
        missing.append({
            "element": element,
            "name": target.get("name"),
            "position": position,
            "elite_percentage": target.get("elite_percentage", target.get("percentage")),
            "cost": cost,
            "cash_affordable_with_one_move": cash_affordable,
        })
    outside = [
        {"id": player.get("id"), "name": player.get("name"), "position": player.get("position")}
        for player in squad if int(player.get("id", -1)) not in template_ids
    ]
    return {
        "formation": competitive.get("template_formation"),
        "owned": [{"element": p.get("element"), "name": p.get("name")} for p in owned_template],
        "missing": missing,
        "outside": outside,
    }


def build_decision_summary(plan, *, squad, final_squad, candidates, starters,
                           gw_ids, bank, free_transfers, paid_transfers_calibrated,
                           paid_transfer_min_gws, calibration, generated_at,
                           deadline, solver_settings, free_transfers_synced=True,
                           horizon_plan=None, captain_rankings=None,
                           source_manifest=None, team_diff=None):
    """Return an additive, JSON-safe explanation of the canonical decision."""
    risk_penalty = float(solver_settings.get("risk_penalty", 0.25))
    bench_weight = float(solver_settings.get("bench_weight", 0.08))
    transfers = plan.get("transfers") or []
    current = squad_horizon_breakdown(squad, risk_penalty, bench_weight)
    proposed = squad_horizon_breakdown(final_squad, risk_penalty, bench_weight)
    rows = []
    for index, gw in enumerate(gw_ids):
        cur = current[index] if index < len(current) else 0.0
        new = proposed[index] if index < len(proposed) else 0.0
        rows.append({
            "gw": gw, "weight": (1.0, 0.7, 0.5)[index],
            "current": round(cur, 1), "proposed": round(new, 1),
            "gain": round(new - cur, 1),
        })

    chosen_formation = formation(starters)
    template_formation = ((plan.get("competitive") or {}).get("template_formation"))
    if chosen_formation != template_formation and template_formation:
        formation_reason = (
            f"GW{gw_ids[0]} projections select {chosen_formation}; the {template_formation} "
            "elite shape is a transfer destination, not a forced weekly lineup."
        )
    else:
        formation_reason = f"The projected XI and elite template both use {chosen_formation}."

    next_free = _hypothetical_route(
        squad, candidates, bank, 1, max_joint_transfers=1,
        risk_penalty=risk_penalty, bench_weight=bench_weight, horizon_offset=1)
    two_free = _hypothetical_route(
        squad, candidates, bank, 2, max_joint_transfers=2,
        risk_penalty=risk_penalty, bench_weight=bench_weight, horizon_offset=2)
    paid = _best_paid_candidate(
        squad, candidates, bank, risk_penalty=risk_penalty, bench_weight=bench_weight)
    if next_free and len(gw_ids) > 1:
        next_free["projection_starts_gw"] = gw_ids[1]
    if two_free and len(gw_ids) > 2:
        two_free["projection_starts_gw"] = gw_ids[2]
    if paid:
        paid["projection_starts_gw"] = gw_ids[0]

    template_gate_applied = bool(
        ((plan.get("competitive") or {}).get("candidate_gate") or {}).get("applied")
    )
    safe_mode = (source_manifest or {}).get("status") == "lineup_only_safe"
    if safe_mode:
        action = "LINEUP ONLY"
        failures = (source_manifest or {}).get("refresh_failures") or ["competitive refresh"]
        reason = "Transfers locked because this run did not refresh every required source: " + ", ".join(failures) + "."
        approval_scope = "Approval changes only the XI, captain and vice-captain; no transfer will be made."
    elif transfers:
        action = "TRANSFER"
        pool = " elite-template" if template_gate_applied else ""
        reason = f"The selected legal{pool} package clears the risk-adjusted three-GW threshold."
        approval_scope = f"Approval applies {len(transfers)} transfer(s), the XI, captain and vice-captain."
    elif free_transfers <= 0 and not paid_transfers_calibrated:
        action = "HOLD"
        reason = (
            f"You have 0 free transfers. Paid moves are locked until "
            f"{paid_transfer_min_gws} completed GWs calibrate V4."
        )
        approval_scope = "Approval changes only the XI, captain and vice-captain; no transfer will be made."
    elif free_transfers <= 0:
        action = "HOLD"
        reason = "You have 0 free transfers and no paid move cleared its hit-adjusted threshold."
        approval_scope = "Approval changes only the XI, captain and vice-captain; no transfer will be made."
    else:
        action = "ROLL"
        reason = f"No legal move cleared the three-GW threshold; roll {free_transfers} free transfer(s)."
        approval_scope = "Approval changes only the XI, captain and vice-captain; no transfer will be made."
    if not transfers and team_diff is not None and not team_diff.get("write_required"):
        approval_scope = "No FPL write is required; approval only acknowledges the current hold decision."

    xi_mean = sum(float(player.get("xpts", 0) or 0) for player in starters)
    captain = plan.get("captain") or {}
    captain_player = next((p for p in starters if p.get("id") == captain.get("id")), None)
    captain_mean = float((captain_player or {}).get("xpts", 0) or 0)
    variance = sum(float(player.get("xpts_variance", 0) or 0) for player in starters)
    variance += float((captain_player or {}).get("xpts_variance", 0) or 0)
    outcome_mean = xi_mean + captain_mean
    outcome_sd = math.sqrt(max(0.0, variance))

    roadmap = []
    for index, week in enumerate((horizon_plan or {}).get("weeks") or []):
        moves = [{"out": move.get("out_name"), "in": move.get("in_name"),
                  "hit": index >= max(0, len(week.get("transfers") or []) - int(week.get("hits") or 0))}
                 for index, move in enumerate(week.get("transfers") or [])]
        route = ({"moves": moves, "horizon_gain": 0.0} if moves else None)
        roadmap.append({
            "gw": gw_ids[index] if index < len(gw_ids) else gw_ids[0] + index,
            "action": (action if index == 0 else ("TRANSFER" if moves else "ROLL / HOLD")),
            "route": route, "status": "recommended" if index == 0 else "conditional",
            "formation": week.get("formation"),
            "bank_after": round(float(week.get("bank_after") or 0) / 10, 1),
            "free_transfers_before": week.get("free_transfers_before"),
            "free_transfers_after": week.get("free_transfers_after"),
            "mean_points_with_captain": week.get("mean_points_with_captain"),
            "robust_points_with_captain": week.get("robust_points_with_captain"),
        })
    if not roadmap:
        roadmap = [{"gw": gw_ids[0], "action": action, "route": _route(transfers), "status": "recommended"}]

    competitive_meta = ((plan.get("competitive") or {}).get("meta") or {})
    cutoff_minutes = int(solver_settings.get("approval_cutoff_minutes", 30))
    try:
        generated_dt = datetime.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        deadline_dt = datetime.datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        minutes_to_deadline = max(0.0, (deadline_dt - generated_dt).total_seconds() / 60.0)
        deadline_safety = "open" if minutes_to_deadline > cutoff_minutes else "locked"
    except (TypeError, ValueError):
        minutes_to_deadline = None
        deadline_safety = "unknown"
    return {
        "schema_version": 2,
        "run_id": plan.get("run_id"),
        "plan_id": plan.get("plan_id"),
        "optimizer": {
            "name": (horizon_plan or {}).get("optimizer", "legacy"),
            "version": (horizon_plan or {}).get("optimizer_version", plan.get("optimizer_version")),
            "status": (horizon_plan or {}).get("status"),
            "objective": (horizon_plan or {}).get("objective"),
            "candidate_pool_size": (horizon_plan or {}).get("candidate_pool_size"),
        },
        "recommended_action": action,
        "reason": reason,
        "approval_scope": approval_scope,
        "template_candidate_gate_applied": template_gate_applied,
        "formation": {
            "selected": chosen_formation,
            "template": template_formation,
            "explanation": formation_reason,
        },
        "horizon": {
            "metric": "risk-adjusted legal XI + captain + bench utility",
            "rows": rows,
            "current_weighted": round(sum(row["weight"] * row["current"] for row in rows), 1),
            "proposed_weighted": round(sum(row["weight"] * row["proposed"] for row in rows), 1),
        },
        "alternatives": {
            "hold": {"horizon_gain": 0.0},
            "next_free_transfer": next_free,
            "two_free_transfers": two_free,
            "best_paid_transfer": paid,
            "paid_transfer_allowed": bool(paid_transfers_calibrated),
        },
        "roadmap": roadmap,
        "captain_rankings": list(captain_rankings or [])[:3],
        "team_diff": team_diff or {},
        "source_manifest": source_manifest or {},
        "uncertainty": {
            "mean_with_captain": round(outcome_mean, 1),
            "outcome_low": round(max(0.0, outcome_mean - 1.28 * outcome_sd), 1),
            "outcome_high": round(outcome_mean + 1.28 * outcome_sd, 1),
            "label": "approximate 80% outcome range, not a guarantee",
            "calibration": calibration or {},
        },
        "template_comparison": _template_comparison(plan, final_squad, bank),
        "data_health": {
            "official_fpl_snapshot_at": generated_at,
            "account_snapshot_at": generated_at,
            "account_squad_synced": len(squad) == 15,
            "free_transfers_synced": bool(free_transfers_synced),
            "free_transfers": free_transfers,
            "league_snapshot_at": competitive_meta.get("snapshot_at") or competitive_meta.get("generated_at"),
            "league_snapshot_age_hours": competitive_meta.get("freshness_hours"),
            "league_context_ready": (plan.get("competitive") or {}).get("context_status") == "ready",
            "deadline": deadline,
            "minutes_to_deadline": round(minutes_to_deadline, 1) if minutes_to_deadline is not None else None,
            "approval_cutoff_minutes": cutoff_minutes,
            "deadline_safety": deadline_safety,
        },
    }
