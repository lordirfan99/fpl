"""Adaptive opponent intelligence for multi-league FPL decisions.

Historical scouting chooses whom to monitor.  Locked, post-deadline picks and
completed-GW evidence then refine that prior.  Opponent data may alter
captaincy variance only inside explicit xPts guardrails; it never changes
player projections, transfers, the approval gate, or pre-deadline pick trust.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections import defaultdict


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SCOUT_FILE = os.path.join(BASE, "data", "research", "fpl_league_scout_2026-08-20.json")
LATEST_STATE_FILE = os.path.join(BASE, "data", "processed", "league_intelligence", "latest.json")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def load_scout_priors(path=None):
    """Return {entry_id: compact preseason prior}; fail soft when absent."""
    payload = _read_json(path or DEFAULT_SCOUT_FILE) or {}
    priors = {}
    for scout in payload.get("scouts", []) or []:
        try:
            entry_id = int(scout["entry_id"])
        except (KeyError, TypeError, ValueError):
            continue
        metrics = scout.get("metrics", {}) or {}
        memberships = scout.get("memberships", []) or []
        priors[entry_id] = {
            "entry": entry_id,
            "team_name": scout.get("team_name"),
            "manager_name": scout.get("manager_name"),
            "historical_score": float(metrics.get("scout_score", 50.0) or 50.0),
            "tier": metrics.get("threat_tier", "D"),
            "confidence": metrics.get("confidence", "Low"),
            "archetype": metrics.get("archetype", "Unknown"),
            "seasons": int(metrics.get("seasons_played", 0) or 0),
            "leagues": sorted({int(m["league_id"]) for m in memberships if m.get("league_id")}),
        }
    return priors


def select_deep_cohort(standings, priors, our_entry, *, max_size=40,
                       top_per_league=6, sharp_slots=8, proximity_slots=8,
                       pinned=None):
    """Choose a bounded, deduplicated deep-monitoring cohort.

    ``standings`` is a list of normalized membership rows containing entry,
    league_id, rank and total.  Selection order is deterministic: pinned,
    league leaders, strongest historical priors, closest point gaps, then
    shared S/A entries.  A manager appearing in several leagues is one entry.
    """
    our_entry = int(our_entry)
    pinned = [int(x) for x in (pinned or []) if int(x) != our_entry]
    by_entry = defaultdict(list)
    by_league = defaultdict(list)
    for raw in standings or []:
        try:
            row = dict(raw)
            row["entry"] = int(row["entry"])
            row["league_id"] = int(row["league_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if row["entry"] == our_entry:
            continue
        by_entry[row["entry"]].append(row)
        by_league[row["league_id"]].append(row)

    selected = []
    reasons = defaultdict(set)

    def add(entry_id, reason):
        entry_id = int(entry_id)
        if entry_id == our_entry or entry_id not in by_entry:
            return
        reasons[entry_id].add(reason)
        if entry_id not in selected and len(selected) < max_size:
            selected.append(entry_id)

    for entry_id in pinned:
        add(entry_id, "pinned")

    for league_id in sorted(by_league):
        rows = [r for r in by_league[league_id] if r.get("rank")]
        rows.sort(key=lambda r: (int(r.get("rank") or 10**9), int(r["entry"])))
        for row in rows[:top_per_league]:
            add(row["entry"], f"top_{top_per_league}_league_{league_id}")

    sharp = sorted(
        (p for eid, p in priors.items() if eid in by_entry and eid != our_entry),
        key=lambda p: (-float(p.get("historical_score", 50)), -int(p.get("seasons", 0)), p["entry"]),
    )
    for prior in sharp[:sharp_slots]:
        add(prior["entry"], "historical_sharpness")

    our_totals = {}
    for raw in standings or []:
        try:
            if int(raw["entry"]) == our_entry and raw.get("total") is not None:
                our_totals[int(raw["league_id"])] = float(raw["total"])
        except (KeyError, TypeError, ValueError):
            continue
    proximity = []
    for entry_id, rows in by_entry.items():
        gaps = []
        for row in rows:
            league_id = row["league_id"]
            if league_id in our_totals and row.get("total") is not None:
                gaps.append(abs(float(row["total"]) - our_totals[league_id]))
        if gaps:
            proximity.append((min(gaps), entry_id))
    for _, entry_id in sorted(proximity)[:proximity_slots]:
        add(entry_id, "points_proximity")

    shared_sharp = [
        (entry_id, prior) for entry_id, prior in priors.items()
        if entry_id in by_entry and len(by_entry[entry_id]) > 1 and prior.get("tier") in {"S", "A"}
    ]
    shared_sharp.sort(key=lambda item: (-len(by_entry[item[0]]), -item[1]["historical_score"], item[0]))
    for entry_id, _ in shared_sharp:
        add(entry_id, "shared_league_threat")

    result = []
    for entry_id in selected:
        prior = priors.get(entry_id, {})
        rows = by_entry[entry_id]
        result.append({
            "entry": entry_id,
            "team_name": prior.get("team_name") or rows[0].get("entry_name"),
            "manager_name": prior.get("manager_name") or rows[0].get("player_name"),
            "historical_score": float(prior.get("historical_score", 50.0)),
            "tier": prior.get("tier", "D"),
            "prior_confidence": prior.get("confidence", "Low"),
            "archetype": prior.get("archetype", "Unknown"),
            "leagues": sorted({int(r["league_id"]) for r in rows}),
            "reasons": sorted(reasons[entry_id]),
        })
    return result


def adaptive_mode(completed_gws, reference_gap, *, total_gws=38):
    """Return Protect/Neutral/Chase from completed evidence and point gap.

    ``reference_gap`` is rival points minus our points.  The early season is
    always Neutral.  Variance modes activate only in the final ten GWs.
    """
    completed = max(0, int(completed_gws or 0))
    remaining = max(1, int(total_gws) - completed)
    if completed < 4 or reference_gap is None or remaining > 10:
        return {"mode": "Neutral", "remaining_gws": remaining,
                "required_swing_per_gw": None if reference_gap is None else round(float(reference_gap) / remaining, 2),
                "reason": "Expected points remain primary; variance mode is not justified yet."}
    gap = float(reference_gap)
    swing = gap / remaining
    if swing > 1.0:
        return {"mode": "Chase", "remaining_gws": remaining,
                "required_swing_per_gw": round(swing, 2),
                "reason": "Catch-up requirement exceeds one point per remaining GW."}
    if gap < 0 and abs(swing) <= 2.0:
        return {"mode": "Protect", "remaining_gws": remaining,
                "required_swing_per_gw": round(swing, 2),
                "reason": "We lead the closest sharp rival; control avoidable captain variance."}
    return {"mode": "Neutral", "remaining_gws": remaining,
            "required_swing_per_gw": round(swing, 2),
            "reason": "The points gap does not justify sacrificing expected points."}


def validate_locked_picks(payload, requested_event):
    """Validate the public post-deadline picks structure before using it."""
    if not isinstance(payload, dict) or int(payload.get("entry_history", {}).get("event", requested_event) or requested_event) != int(requested_event):
        return False
    picks = payload.get("picks") or []
    if len(picks) != 15:
        return False
    elements = [p.get("element") for p in picks]
    if any(not isinstance(e, int) for e in elements) or len(set(elements)) != 15:
        return False
    captains = [p for p in picks if p.get("is_captain")]
    vice = [p for p in picks if p.get("is_vice_captain")]
    if len(captains) != 1 or len(vice) != 1:
        return False
    return all(isinstance(p.get("multiplier"), int) and p.get("multiplier") >= 0 for p in picks)


def exposure_from_picks(cohort, picks_by_entry, element_names=None):
    """Weighted locked-squad ownership and effective ownership by player."""
    element_names = element_names or {}
    weights = {int(c["entry"]): 1.0 + float(c.get("historical_score", 50.0)) / 100.0 for c in cohort}
    denominator = sum(weights[eid] for eid in weights if eid in picks_by_entry)
    if denominator <= 0:
        return {}
    totals = defaultdict(lambda: {"owned": 0.0, "effective": 0.0, "captained": 0.0, "count": 0})
    for entry_id, payload in picks_by_entry.items():
        entry_id = int(entry_id)
        if entry_id not in weights:
            continue
        weight = weights[entry_id]
        for pick in payload.get("picks", []) or []:
            element = int(pick["element"])
            row = totals[element]
            row["owned"] += weight
            row["effective"] += weight * int(pick.get("multiplier", 0))
            row["captained"] += weight if pick.get("is_captain") else 0.0
            row["count"] += 1
    return {
        str(element): {
            "element": element,
            "name": element_names.get(element, str(element)),
            "ownership": round(100.0 * row["owned"] / denominator, 1),
            "effective_ownership": round(100.0 * row["effective"] / denominator, 1),
            "captain_share": round(100.0 * row["captained"] / denominator, 1),
            "manager_count": row["count"],
        }
        for element, row in totals.items()
    }


def elite_template_current_season(cohort, picks_by_entry, standings, element_names=None,
                                  *, top_fraction=0.25, min_managers=6,
                                  ownership_floor=50.0):
    """Elite template from THIS SEASON's evidence only - no preseason prior.

    Rank the cohort by current-season league total (best across the manager's
    leagues), keep the strongest ``top_fraction`` (at least ``min_managers``),
    then aggregate their locked squads with EQUAL weight. A player is
    "template" when at least ``ownership_floor`` percent of that elite subset
    own them. Shape mirrors the scout ``elite_template`` rows so the gate can
    consume either.
    """
    element_names = element_names or {}
    best_total = {}
    for row in standings or []:
        try:
            entry_id = int(row["entry"])
            total = float(row.get("total") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        best_total[entry_id] = max(best_total.get(entry_id, 0.0), total)

    ranked = sorted(
        (int(c["entry"]) for c in cohort if int(c["entry"]) in picks_by_entry),
        key=lambda eid: (-best_total.get(eid, 0.0), eid),
    )
    if not ranked:
        return {"source": "current_season", "manager_count": 0, "players": []}

    keep = max(int(min_managers), int(round(len(ranked) * float(top_fraction))))
    elite = ranked[:keep] or ranked[:1]
    denom = len(elite)
    tally = defaultdict(lambda: {"own": 0, "cap": 0})
    for entry_id in elite:
        for pick in (picks_by_entry.get(entry_id, {}) or {}).get("picks", []) or []:
            element = int(pick["element"])
            tally[element]["own"] += 1
            if pick.get("is_captain"):
                tally[element]["cap"] += 1

    players = sorted(
        (
            {
                "element": element,
                "name": element_names.get(element, str(element)),
                "elite_percentage": round(100.0 * row["own"] / denom, 1),
                "elite_captaincy": round(100.0 * row["cap"] / denom, 1),
            }
            for element, row in tally.items()
            if 100.0 * row["own"] / denom >= float(ownership_floor)
        ),
        key=lambda p: -p["elite_percentage"],
    )
    return {
        "source": "current_season",
        "manager_count": denom,
        "top_fraction": float(top_fraction),
        "ownership_floor": float(ownership_floor),
        "players": players,
    }


def load_latest_state(path=None, *, max_age_hours=30, now=None):
    state = _read_json(path or LATEST_STATE_FILE)
    if not state:
        return None
    try:
        timestamp = dt.datetime.fromisoformat(str(state["as_of"]).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return None
        now = now or dt.datetime.now(dt.timezone.utc)
        if (now - timestamp).total_seconds() > max_age_hours * 3600:
            return None
    except Exception:
        return None
    return state


def refine_plan_captain(plan, state=None, *, protect_guardrail=0.5, chase_guardrail=1.0):
    """Apply bounded league-mode captain refinement to a pending plan.

    Protect may lose at most 0.5 xPts to match high threat EO. Chase may lose
    at most 1.0 xPts for a lower-owned captain. Neutral never changes captain.
    Returns a copied plan with an audit record.
    """
    updated = dict(plan or {})
    state = state or load_latest_state()
    audit = {"applied": False, "mode": "Neutral", "reason": "No fresh trusted league intelligence."}
    if not state or int(state.get("event", -1)) not in {int(updated.get("gw", -2)), int(updated.get("gw", -2)) - 1}:
        updated["league_intelligence"] = audit
        return updated

    mode_info = state.get("mode") or {}
    mode = mode_info.get("mode", "Neutral")
    audit = {"applied": False, "mode": mode, "reason": mode_info.get("reason")}
    starters = updated.get("target_starters") or []
    if mode == "Neutral" or not starters:
        updated["league_intelligence"] = audit
        return updated
    exposure = state.get("player_exposure") or {}
    candidates = []
    best_xpts = max(float(p.get("xpts", 0) or 0) for p in starters)
    guardrail = float(protect_guardrail if mode == "Protect" else chase_guardrail)
    for player in starters:
        xpts = float(player.get("xpts", 0) or 0)
        if best_xpts - xpts > guardrail + 1e-9:
            continue
        eo = exposure.get(str(player.get("id")), {}) or {}
        candidates.append((player, float(eo.get("captain_share", 0) or 0), float(eo.get("effective_ownership", 0) or 0)))
    if not candidates:
        updated["league_intelligence"] = audit
        return updated
    if mode == "Protect":
        chosen, cap_share, eo = max(candidates, key=lambda row: (row[1], row[2], float(row[0].get("xpts", 0))))
        if cap_share < 30.0:
            audit["reason"] = "Protect mode active, but no captain concentration passed 30%."
            updated["league_intelligence"] = audit
            return updated
    else:
        chosen, cap_share, eo = min(candidates, key=lambda row: (row[1], -float(row[0].get("xpts", 0))))

    old = updated.get("captain") or {}
    if chosen.get("id") != old.get("id"):
        updated["captain"] = chosen
        remaining = [p for p in starters if p.get("id") != chosen.get("id")]
        if remaining:
            updated["vice"] = max(remaining, key=lambda p: float(p.get("xpts", 0) or 0))
        audit.update({
            "applied": True,
            "from": old.get("name"),
            "to": chosen.get("name"),
            "xpts_cost": round(best_xpts - float(chosen.get("xpts", 0) or 0), 2),
            "captain_share": round(cap_share, 1),
            "effective_ownership": round(eo, 1),
            "reason": f"{mode} captain refinement stayed inside the {guardrail:.1f} xPts guardrail.",
        })
    updated["league_intelligence"] = audit
    return updated
