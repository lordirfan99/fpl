from __future__ import annotations

from collections import Counter
from typing import Any


MODEL_VERSION = "competitive-v4.0"
CORE_OWNERSHIP_THRESHOLD = 60.0


def elite_managers(
    managers: list[dict[str, Any]],
    percentile: int = 5,
    population_size: int | None = None,
) -> list[dict[str, Any]]:
    population = population_size or len(managers)
    count = min(len(managers), max(1, (population * percentile + 99) // 100))
    return sorted(managers, key=lambda item: item.get("overall_rank") or 10**12)[:count]


def cohort_summary(elite: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    owned: Counter[tuple[int, str, str, str]] = Counter()
    captained: Counter[tuple[int, str, str, str]] = Counter()
    for manager in elite:
        for pick in manager.get("squad", []):
            key = (pick["element"], pick["name"], pick["position"], pick["team"])
            owned[key] += 1
            if pick.get("is_captain"):
                captained[key] += 1
    denominator = max(1, len(elite))

    def rows(counter: Counter[tuple[int, str, str, str]]) -> list[dict[str, Any]]:
        return [
            {
                "element": key[0], "name": key[1], "position": key[2], "team": key[3],
                "count": count, "percentage": round(count / denominator * 100, 1),
            }
            for key, count in counter.most_common()
        ]

    return rows(owned), rows(captained)


def _formation(manager: dict[str, Any]) -> str:
    counts = {"DEF": 0, "MID": 0, "FWD": 0}
    for pick in (manager.get("squad") or [])[:11]:
        if pick.get("multiplier", 1) and pick.get("position") in counts:
            counts[pick["position"]] += 1
    return f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}"


def _elite_template(elite: list[dict[str, Any]], ownership: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the consensus XI/bench hierarchy used by the elite cohort."""
    denominator = max(1, len(elite))
    starters: dict[int, int] = Counter()
    for manager in elite:
        for pick in (manager.get("squad") or [])[:11]:
            try:
                starters[int(pick["element"])] += 1
            except (KeyError, TypeError, ValueError):
                continue
    result = []
    for row in ownership[:15]:
        item = dict(row)
        item["elite_percentage"] = item.get("percentage", 0.0)
        item["starter_percentage"] = round(100.0 * starters.get(int(item["element"]), 0) / denominator, 1)
        result.append(item)
    return result


def _transfer_consensus(elite: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str]] = Counter()
    for manager in elite:
        for transfer in manager.get("transfer_details", []) or []:
            out_name = (transfer.get("out_name") or transfer.get("out")
                        or transfer.get("element_out"))
            in_name = (transfer.get("in_name") or transfer.get("in")
                       or transfer.get("element_in"))
            if out_name and in_name:
                counts[(str(out_name), str(in_name))] += 1
    denominator = max(1, len(elite))
    return [{"name": f"{out} → {incoming}", "count": count,
             "percentage": round(100.0 * count / denominator, 1)}
            for (out, incoming), count in counts.most_common(10)]


def calibration_weights(gameweek: int) -> dict[str, float]:
    """V4 weights used directly in every player's competitive score."""
    if gameweek <= 2:
        return {"elite_consensus": 0.45, "projection": 0.45, "current_season_evidence": 0.10}
    if gameweek <= 4:
        return {"elite_consensus": 0.40, "projection": 0.45, "current_season_evidence": 0.15}
    if gameweek <= 8:
        return {"elite_consensus": 0.30, "projection": 0.45, "current_season_evidence": 0.25}
    return {"elite_consensus": 0.25, "projection": 0.45, "current_season_evidence": 0.30}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _role(elite_owned: float, model_support: bool, risk: bool) -> str:
    if risk:
        return "AVOID"
    if elite_owned >= CORE_OWNERSHIP_THRESHOLD and model_support:
        return "ALIGN"
    if elite_owned < 35 and model_support:
        return "CONTROLLED_EDGE"
    if elite_owned >= CORE_OWNERSHIP_THRESHOLD and not model_support:
        return "INVESTIGATE"
    if elite_owned < 20 and not model_support:
        return "AVOID"
    return "NEUTRAL"


def _phase(
    manager: dict[str, Any], managers: list[dict[str, Any]], gameweek: int, alignment: float, target: int
) -> tuple[str, str, dict[str, Any]]:
    leader_points = max((int(item.get("total_points") or 0) for item in managers), default=0)
    manager_points = int(manager.get("total_points") or 0)
    leader_gap = max(0, leader_points - manager_points)
    remaining = max(0, 38 - gameweek)
    chase_trigger = max(40, remaining * 5)
    inputs = {
        "leader_points": leader_points, "manager_points": manager_points, "leader_gap": leader_gap,
        "remaining_gameweeks": remaining, "chase_trigger": chase_trigger,
    }
    if gameweek >= 28 and leader_gap >= chase_trigger:
        return (
            "CHASE",
            "Late-season league deficit clears the V4 chase threshold: accept only calculated, model-supported variance.",
            inputs,
        )
    if gameweek <= 4 and alignment < target:
        return (
            "CATCH",
            "Under-aligned with the validated elite core: converge before taking unnecessary variance.",
            inputs,
        )
    if alignment >= target:
        return (
            "MATCH",
            "Core structure is competitive: preserve the baseline and use only model-supported deviations.",
            inputs,
        )
    return (
        "ATTACK",
        "Alignment is below target outside the early catch window: use selective leverage rather than blind convergence.",
        inputs,
    )


def _current_team_by_element(bootstrap: dict[str, Any]) -> dict[int, str]:
    """Map every player id to the club name it currently belongs to."""
    team_name = {
        int(team["id"]): team.get("name")
        for team in bootstrap.get("teams", [])
        if team.get("id") is not None
    }
    current: dict[int, str] = {}
    for player in bootstrap.get("elements", []):
        try:
            element_id = int(player["id"])
        except (KeyError, TypeError, ValueError):
            continue
        name = team_name.get(int(player.get("team") or 0))
        if name:
            current[element_id] = name
    return current


def _normalize_squad_teams(
    managers: list[dict[str, Any]], current_team: dict[int, str]
) -> list[dict[str, Any]]:
    """Re-stamp each stored squad pick's club from the live catalogue.

    League snapshots freeze ``pick["team"]`` at gameweek finalization. When a
    player changes club afterwards the frozen string is wrong, and it is used
    to route the fixture/FDR lookup, the 3-per-club transfer check and the
    club shown on the decision board. The element id is stable, so the live
    bootstrap is trusted for the club. Non-mutating: only rewritten entries
    are copied.
    """
    if not current_team:
        return managers
    patched: list[dict[str, Any]] = []
    for entry in managers:
        squad = entry.get("squad")
        if not squad:
            patched.append(entry)
            continue
        new_squad: list[dict[str, Any]] = []
        changed = False
        for pick in squad:
            try:
                element_id = int(pick.get("element"))
            except (TypeError, ValueError):
                new_squad.append(pick)
                continue
            club = current_team.get(element_id)
            if club and club != pick.get("team"):
                pick = {**pick, "team": club}
                changed = True
            new_squad.append(pick)
        patched.append({**entry, "squad": new_squad} if changed else entry)
    return patched


def build_recommendations(
    manager: dict[str, Any],
    managers: list[dict[str, Any]],
    bootstrap: dict[str, Any],
    fixtures: list[dict[str, Any]],
    population_size: int | None = None,
    gameweek: int = 1,
    bank: float | None = None,
) -> dict[str, Any]:
    """Build the competitive packet.

    ``bank`` is the manager's bank in pounds. Pass ``None`` (the default) to read
    ``manager["gw_bank"]`` (FPL-native tenths). When neither is available the
    affordability filter runs at zero bank -- which can only *withhold* a
    suggestion, never invent an unaffordable one -- and every transfer is
    stamped ``bank_known=False`` so the caller can flag it.
    """
    current_team = _current_team_by_element(bootstrap)
    managers = _normalize_squad_teams(managers, current_team)
    manager = next(
        (entry for entry in managers if entry.get("entry_id") == manager.get("entry_id")),
        _normalize_squad_teams([manager], current_team)[0],
    )
    elite = elite_managers(managers, population_size=population_size)
    ownership, captaincy = cohort_summary(elite)
    elite_template = _elite_template(elite, ownership)
    formation_counts = Counter(_formation(manager) for manager in elite)
    template_formation = formation_counts.most_common(1)[0][0] if formation_counts else "3-4-3"
    transfer_consensus = _transfer_consensus(elite)
    ownership_by_id = {row["element"]: row["percentage"] for row in ownership}
    captaincy_by_id = {row["element"]: row["percentage"] for row in captaincy}
    player_index = {player["id"]: player for player in bootstrap.get("elements", [])}
    weights = calibration_weights(gameweek)
    fixture_by_team: dict[str, tuple[str, int]] = {}
    for fixture in fixtures:
        home, away = fixture.get("team_h"), fixture.get("team_a")
        if home and away:
            fixture_by_team[home] = (f"{away} (H)", fixture.get("team_h_difficulty", 0))
            fixture_by_team[away] = (f"{home} (A)", fixture.get("team_a_difficulty", 0))

    known_picks: dict[int, dict[str, Any]] = {}
    for entry in managers:
        for pick in entry.get("squad", []):
            known_picks[pick["element"]] = pick

    def signal(pick: dict[str, Any]) -> dict[str, Any]:
        player = player_index.get(pick["element"], {})
        fixture, fdr = fixture_by_team.get(pick["team"], ("Fixture TBC", None))
        xpts = _number(player.get("ep_next"))
        form = _number(player.get("form"))
        points_per_game = _number(player.get("points_per_game"))
        elite_owned = ownership_by_id.get(pick["element"], 0.0)
        elite_captained = captaincy_by_id.get(pick["element"], 0.0)
        chance = player.get("chance_of_playing_next_round")
        risk = player.get("status", "a") != "a" or (chance is not None and chance < 75)

        elite_component = _clamp((elite_owned * 0.80 + elite_captained * 0.20) / 100)
        fixture_component = _clamp((6 - fdr) / 5) if fdr else 0.5
        projection_component = _clamp(_clamp(xpts / 8) * 0.85 + fixture_component * 0.15)
        current_component = _clamp(_clamp(form / 8) * 0.65 + _clamp(points_per_game / 8) * 0.35)
        competitive_score = 100 * (
            weights["elite_consensus"] * elite_component
            + weights["projection"] * projection_component
            + weights["current_season_evidence"] * current_component
        )
        if risk:
            competitive_score -= 35
        model_support = not risk and projection_component >= 0.50 and (gameweek <= 2 or current_component >= 0.35)
        return {
            **pick,
            "xpts": round(xpts, 2), "form": round(form, 2), "points_per_game": round(points_per_game, 2),
            "elite_ownership": elite_owned, "elite_captaincy": elite_captained,
            "fixture": fixture, "fdr": fdr, "risk": risk, "news": player.get("news", ""),
            "model_support": model_support,
            "elite_core": elite_owned >= CORE_OWNERSHIP_THRESHOLD,
            "role": _role(elite_owned, model_support, risk),
            "components": {
                "elite_consensus": round(elite_component, 4),
                "projection": round(projection_component, 4),
                "current_season_evidence": round(current_component, 4),
            },
            "score": round(max(0, competitive_score), 2),
        }

    signals = [signal(pick) for pick in known_picks.values()]
    by_id = {item["element"]: item for item in signals}
    squad = [by_id[pick["element"]] for pick in manager.get("squad", []) if pick["element"] in by_id]
    owned_ids = {pick["element"] for pick in squad}
    # FPL orders the legal XI first. Multiplier-based selection would incorrectly
    # include the four substitutes during Bench Boost.
    starters = squad[:11]
    missing = sorted(
        (pick for pick in signals if pick["element"] not in owned_ids and pick["elite_ownership"] >= 20 and not pick["risk"]),
        key=lambda pick: pick["score"], reverse=True,
    )
    weakest: dict[str, dict[str, Any]] = {}
    for pick in squad:
        current = weakest.get(pick["position"])
        if current is None or pick["score"] < current["score"]:
            weakest[pick["position"]] = pick
    transfers = []
    if bank is None:
        raw_bank = manager.get("gw_bank")
        bank_known = raw_bank is not None
        bank = _number(raw_bank) / 10.0
    else:
        bank_known = True
    club_counts = Counter(pick.get("team") for pick in squad)
    for incoming in missing:
        outgoing = weakest.get(incoming["position"])
        if not outgoing:
            continue
        affordable = incoming["cost"] <= outgoing["cost"] + bank + 1e-9
        resulting_club_count = club_counts[incoming["team"]] + (0 if incoming["team"] == outgoing["team"] else 1)
        club_limit_ok = resulting_club_count <= 3
        if not (affordable and club_limit_ok):
            continue
        signal_gain = incoming["score"] - outgoing["score"]
        if signal_gain > 5:
            transfers.append({
                "incoming": incoming, "outgoing": outgoing,
                "xpts_gain": round(incoming["xpts"] - outgoing["xpts"], 2),
                "signal_gain": round(signal_gain, 2),
                "gain_basis": "next_gameweek_gross; transfer cost and hits excluded",
                "net_ev_status": "not_calculated",
                "legal_checks": {"same_position": True, "affordable_at_snapshot_prices": True,
                                 "club_limit": True, "bank": round(bank, 1), "bank_known": bank_known},
            })
    transfers.sort(key=lambda item: item["signal_gain"], reverse=True)

    core = [pick for pick in signals if pick["elite_core"] and not pick["risk"]]
    core_owned = sum(1 for pick in core if pick["element"] in owned_ids)
    alignment = round(core_owned / max(1, len(core)) * 100, 1) if core else 100.0
    target_alignment = 82 if gameweek <= 4 else 78 if gameweek <= 8 else 72
    phase, phase_reason, phase_inputs = _phase(manager, managers, gameweek, alignment, target_alignment)

    competitive = {
        "model_version": MODEL_VERSION, "phase": phase, "phase_reason": phase_reason,
        "phase_inputs": phase_inputs, "alignment": alignment, "target_alignment": target_alignment,
        "core_owned": core_owned, "core_size": len(core),
        "core_ownership_threshold": CORE_OWNERSHIP_THRESHOLD,
        "critical_missing": sorted(
            (pick for pick in core if pick["element"] not in owned_ids and pick["model_support"]),
            key=lambda pick: pick["elite_ownership"], reverse=True,
        )[:6],
        "model_edges": sorted(
            (pick for pick in signals if pick["element"] not in owned_ids and pick["role"] == "CONTROLLED_EDGE"),
            key=lambda pick: pick["score"], reverse=True,
        )[:6],
        "disagreements": sorted(
            (pick for pick in signals if pick["role"] == "INVESTIGATE"),
            key=lambda pick: pick["elite_ownership"], reverse=True,
        )[:6],
        "elite_template": elite_template,
        "template_formation": template_formation,
        "captain_consensus": captaincy[:5],
        "transfer_consensus": transfer_consensus,
        "template_gate": {
            "alignment_threshold": target_alignment,
            "alignment": alignment,
            "differential_allowed": alignment >= target_alignment and bool(
                any(item["role"] == "CONTROLLED_EDGE" for item in signals)
            ),
            "decision": "CONTROLLED_DIFFERENTIAL" if alignment >= target_alignment else "CONVERGE_TO_TEMPLATE",
        },
        "weights": weights,
        "score_definition": "weighted elite consensus + FPL projection/fixture + current form/PPG; 0-100",
        "execution_authority": "manual_fpl", "writes_enabled": False,
    }
    return {
        "elite_count": len(elite),
        "elite_overlap": sum(1 for pick in squad if ownership_by_id.get(pick["element"], 0) > 0),
        "elite_average_points": round(sum(item.get("gw_points", 0) for item in elite) / max(1, len(elite)), 1),
        "transfers": transfers[:5],
        "captains": sorted(starters, key=lambda pick: pick["score"], reverse=True)[:4],
        "risks": [pick for pick in squad if pick["risk"]],
        "missing_elite_players": missing[:6], "competitive": competitive,
        "inputs": {"bank_known": bank_known, "bank": round(bank, 1)},
    }


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
