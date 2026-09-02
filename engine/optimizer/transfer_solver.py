"""FPL Autopilot transfer optimizer.

The live solver evaluates one- and two-transfer packages jointly. Packages are
scored by the best legal XI in each projected gameweek (including captain and a
small bench-depth value), rather than by blindly summing all 15 players.
"""
from itertools import combinations
import math


LINEUP_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
LINEUP_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}


def _club_counts(squad):
    counts = {}
    for player in squad:
        counts[player["club"]] = counts.get(player["club"], 0) + 1
    return counts


def _gw_value(player, gw_index, risk_penalty):
    means = player.get("xpts_by_gw") or []
    variances = player.get("variance_by_gw") or []
    if gw_index < len(means):
        mean = float(means[gw_index] or 0.0)
        variance = float(variances[gw_index] or 0.0) if gw_index < len(variances) else 0.0
        return mean - risk_penalty * math.sqrt(max(0.0, variance))
    if gw_index == 0:
        return float(player.get("risk_adjusted_xpts", player.get("xpts", 0.0)) or 0.0)
    return 0.0


def _lineup_utility(squad, gw_index, risk_penalty=0.25, bench_weight=0.08):
    """Best legal XI + captain + modest autosub insurance for one GW."""
    if len(squad) < 11:
        return sum(_gw_value(player, gw_index, risk_penalty) for player in squad)
    values = {player["id"]: _gw_value(player, gw_index, risk_penalty) for player in squad}
    ranked = {}
    for position in LINEUP_MIN:
        ranked[position] = sorted(
            (player for player in squad if player["position"] == position),
            key=lambda player: values[player["id"]], reverse=True)
    best = None
    for defenders in range(3, 6):
        for midfielders in range(2, 6):
            forwards = 10 - defenders - midfielders
            if not 1 <= forwards <= 3:
                continue
            counts = {"GKP": 1, "DEF": defenders, "MID": midfielders, "FWD": forwards}
            if any(len(ranked[pos]) < count for pos, count in counts.items()):
                continue
            xi = [player for pos, count in counts.items() for player in ranked[pos][:count]]
            xi_value = sum(values[player["id"]] for player in xi)
            captain_value = max(values[player["id"]] for player in xi)
            starter_ids = {player["id"] for player in xi}
            bench_value = sum(max(0.0, values[player["id"]]) for player in squad
                              if player["id"] not in starter_ids)
            score = xi_value + captain_value + bench_weight * bench_value
            if best is None or score > best:
                best = score
    return best if best is not None else sum(values.values())


def squad_horizon_utility(squad, risk_penalty=0.25, bench_weight=0.08,
                          horizon_weights=(1.0, 0.7, 0.5)):
    has_gw_arrays = any(player.get("xpts_by_gw") for player in squad)
    if not has_gw_arrays:
        return sum(float(player.get("xpts_horizon", 0.0) or 0.0) for player in squad)
    return sum(weight * _lineup_utility(squad, gw, risk_penalty, bench_weight)
               for gw, weight in enumerate(horizon_weights))


def squad_horizon_breakdown(squad, risk_penalty=0.25, bench_weight=0.08,
                            horizon_length=3):
    """Expose the per-GW utility used by the joint optimizer for explanations."""
    return [
        _lineup_utility(squad, gw, risk_penalty, bench_weight)
        for gw in range(max(0, int(horizon_length)))
    ]


def generate_moves(current_squad, candidates, club_max=3, protected=None,
                   candidate_limit_per_out=10):
    """Generate promising same-position swaps; final package legality is checked later."""
    protected = protected or set()
    owned = {player["id"] for player in current_squad}
    moves = []
    for outgoing in current_squad:
        if outgoing["id"] in protected:
            continue
        options = []
        for incoming in candidates:
            if incoming["id"] in owned or incoming["position"] != outgoing["position"]:
                continue
            approximate_gain = (
                float(incoming.get("xpts_horizon", 0.0) or 0.0)
                - float(outgoing.get("xpts_horizon", 0.0) or 0.0)
            )
            options.append({
                "out": outgoing, "in": incoming, "gain": approximate_gain,
                "gain_gw1": float(incoming.get("xpts", 0.0) or 0.0)
                - float(outgoing.get("xpts", 0.0) or 0.0),
                "cost_delta": incoming["cost"]
                - outgoing.get("selling_price", outgoing["cost"]),
            })
        options.sort(key=lambda move: -move["gain"])
        # Keep high-projection replacements plus cheap enablers, because the
        # latter can fund a jointly valuable second transfer.
        selected = options[:candidate_limit_per_out]
        selected.extend(sorted(options, key=lambda move: move["in"]["cost"])[:4])
        seen = set()
        for move in selected:
            incoming_id = move["in"]["id"]
            if incoming_id not in seen:
                moves.append(move)
                seen.add(incoming_id)
    moves.sort(key=lambda move: -move["gain"])
    return moves


def _apply_package(squad, package):
    incoming_by_out = {move["out"]["id"]: move["in"] for move in package}
    result = []
    for player in squad:
        incoming = incoming_by_out.get(player["id"])
        if incoming is None:
            result.append(player)
        else:
            replacement = dict(incoming)
            replacement["selling_price"] = replacement["cost"]
            replacement["purchase_price"] = replacement["cost"]
            result.append(replacement)
    return result


def _legal_package(squad, package, bank, club_max):
    out_ids = [move["out"]["id"] for move in package]
    in_ids = [move["in"]["id"] for move in package]
    if len(set(out_ids)) != len(out_ids) or len(set(in_ids)) != len(in_ids):
        return False
    if sum(move["cost_delta"] for move in package) > bank + 0.01:
        return False
    final_squad = _apply_package(squad, package)
    return max(_club_counts(final_squad).values(), default=0) <= club_max


def _solve_greedy_compat(current_squad, candidates, free_transfers, bank,
                         hit_threshold, club_max, min_gain, protected,
                         max_paid_transfers):
    """Compatibility path for 3–15 banked FTs and explicit unlimited rebuilds.

    Normal one/two-transfer weeks use the joint optimizer below. Large free-
    transfer states cannot be enumerated combinatorially, so they retain the
    audited sequential behavior and the same paid-transfer cap contract.
    """
    squad = list(current_squad)
    market = list(candidates)
    available_bank = bank
    ft_left = max(0, int(free_transfers))
    used_hits = 0
    cap_blocked = 0
    transfers = []
    while True:
        accepted = None
        for move in generate_moves(squad, market, club_max, protected=protected):
            if not _legal_package(squad, (move,), available_bank, club_max):
                continue
            use_hit = ft_left <= 0
            threshold = hit_threshold if use_hit else min_gain
            if move["gain"] <= threshold if use_hit else move["gain"] < threshold:
                continue
            if (use_hit and max_paid_transfers is not None
                    and used_hits >= max_paid_transfers):
                cap_blocked += 1
                continue
            accepted = (move, use_hit)
            break
        if accepted is None:
            break
        move, use_hit = accepted
        transfers.append({
            "element_in": move["in"]["id"], "element_out": move["out"]["id"],
            "purchase_price": move["in"]["cost"],
            "selling_price": move["out"].get("selling_price", move["out"]["cost"]),
            "gain": round(move["gain"], 1), "gain_gw1": round(move["gain_gw1"], 1),
            "out_name": move["out"]["name"], "in_name": move["in"]["name"],
            "out_pos": move["out"]["position"], "hit": use_hit,
        })
        if use_hit:
            used_hits += 1
        else:
            ft_left -= 1
        available_bank -= move["cost_delta"]
        squad = _apply_package(squad, (move,))
        market = [player for player in market if player["id"] != move["in"]["id"]]
    notes = []
    if used_hits:
        notes.append(f"{used_hits} hit(s) taken at -4 each")
    if cap_blocked:
        notes.append(
            f"paid-transfer cap {max_paid_transfers} reached; "
            f"{cap_blocked} further hit move(s) excluded")
    if ft_left:
        notes.append(f"{ft_left} free transfer(s) rolled over")
    return transfers, available_bank, ft_left, notes


def solve_transfers(current_squad, candidates, free_transfers, bank,
                    hit_threshold=5.0, club_max=3, min_gain=1.5, protected=None,
                    max_joint_transfers=2, risk_penalty=0.25, bench_weight=0.08,
                    max_paid_transfers=1):
    """Choose the best legal transfer package, jointly evaluating up to two moves."""
    if int(free_transfers) > 2 or max_paid_transfers is None:
        return _solve_greedy_compat(
            current_squad, candidates, free_transfers, bank, hit_threshold,
            club_max, min_gain, protected, max_paid_transfers)
    squad = list(current_squad)
    moves = generate_moves(squad, candidates, club_max, protected=protected)
    baseline = squad_horizon_utility(squad, risk_penalty, bench_weight)
    best = None
    max_size = min(max(0, int(max_joint_transfers)), 2, len(moves))
    for size in range(1, max_size + 1):
        for package in combinations(moves, size):
            if not _legal_package(squad, package, bank, club_max):
                continue
            hits = max(0, size - max(0, int(free_transfers)))
            if max_paid_transfers is not None and hits > max_paid_transfers:
                continue
            required_gain = hits * hit_threshold if hits else min_gain
            final_squad = _apply_package(squad, package)
            raw_gain = squad_horizon_utility(final_squad, risk_penalty, bench_weight) - baseline
            if (hits and raw_gain <= required_gain) or (not hits and raw_gain < required_gain):
                continue
            net_gain = raw_gain - 4.0 * hits
            if best is None or net_gain > best[0]:
                best = (net_gain, raw_gain, package, hits)

    if best is None:
        notes = []
        if max_paid_transfers == 0 and moves:
            notes.append("paid-transfer cap 0 reached; paid moves excluded")
        if free_transfers:
            notes.append(f"{free_transfers} free transfer(s) rolled over")
        return [], bank, free_transfers, notes

    _, raw_gain, package, hits = best
    transfers = []
    for index, move in enumerate(package):
        transfers.append({
            "element_in": move["in"]["id"], "element_out": move["out"]["id"],
            "purchase_price": move["in"]["cost"],
            "selling_price": move["out"].get("selling_price", move["out"]["cost"]),
            "gain": round(raw_gain if len(package) == 1 else move["gain"], 1),
            "package_gain": round(raw_gain, 1), "gain_gw1": round(move["gain_gw1"], 1),
            "out_name": move["out"]["name"], "in_name": move["in"]["name"],
            "out_pos": move["out"]["position"],
            "hit": index >= max(0, int(free_transfers)),
        })
    new_bank = bank - sum(move["cost_delta"] for move in package)
    ft_left = max(0, int(free_transfers) - len(package))
    notes = [f"joint {len(package)}-move package: +{raw_gain:.1f} lineup-weighted horizon xPts"]
    if hits:
        notes.append(f"{hits} hit(s) taken at -4 each")
    if (max_paid_transfers is not None and hits >= max_paid_transfers
            and len(moves) > len(package)):
        notes.append(f"paid-transfer cap {max_paid_transfers} reached; further hit moves excluded")
    if ft_left:
        notes.append(f"{ft_left} free transfer(s) rolled over")
    return transfers, new_bank, ft_left, notes
