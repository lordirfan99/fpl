from __future__ import annotations

from collections import Counter
from typing import Any


BENCH_BOOST_NAMES = {"bboost", "bench_boost", "benchboost", "bench boost"}
SQUAD_SHAPE = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}


def _pick_position(pick: Any) -> str:
    """Safely coerce a pick's position for shape counting.

    Live/in-progress gameweek snapshots can contain ``null`` picks (managers
    who have not locked a lineup) or entries that are bare strings/non-dicts.
    These must never crash validation — they are quality issues, not 500s.
    """
    if not isinstance(pick, dict):
        return "?"
    return str(pick.get("position") or "?")


def _pick_multiplier(pick: Any) -> int:
    """Safely parse a pick's multiplier, tolerating null and non-numeric
    values that the FPL API can return mid-gameweek."""
    if not isinstance(pick, dict):
        return 0
    raw = pick.get("multiplier")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def validate_manager_squad(manager: dict[str, Any] | None) -> list[str]:
    """Return chip-aware structural issues without mutating snapshot data.

    Designed to fail soft on malformed/live data: a squad entry that is not a
    dict (or a manager that is ``None``) is reported as an issue rather than
    raising the exception that would surface as an HTTP 500.
    """
    if not isinstance(manager, dict) or manager is None:
        return ["manager_entry_malformed"]
    squad = manager.get("squad") or []
    issues: list[str] = []
    non_dict_picks = sum(1 for pick in squad if not isinstance(pick, dict))
    if non_dict_picks:
        issues.append(f"squad_non_dict_picks:{non_dict_picks}")
    # Drop malformed picks for structural counting but keep the count visible.
    squad = [pick for pick in squad if isinstance(pick, dict)]
    if len(squad) != 15:
        issues.append(f"squad_size:{len(squad)}")
        return issues

    shape = Counter(_pick_position(pick) for pick in squad)
    if dict(shape) != SQUAD_SHAPE:
        issues.append(f"squad_shape:{dict(shape)}")

    active_chip = str(
        manager.get("active_chip") or (manager.get("picks") or {}).get("active_chip") or ""
    ).strip().lower()
    bench_boost = active_chip in BENCH_BOOST_NAMES
    scoring_count = sum(1 for pick in squad if _pick_multiplier(pick) > 0)
    expected_scoring = 15 if bench_boost else 11
    if scoring_count != expected_scoring:
        issues.append(f"scoring_players:{scoring_count};expected:{expected_scoring};chip:{active_chip or 'none'}")

    # The FPL picks payload is ordered XI first, then bench. Validate the legal XI
    # independently of Bench Boost, where all fifteen multipliers are positive.
    lineup = squad[:11]
    lineup_shape = Counter(_pick_position(pick) for pick in lineup)
    if lineup_shape["GKP"] != 1:
        issues.append(f"starting_goalkeepers:{lineup_shape['GKP']}")
    if not 3 <= lineup_shape["DEF"] <= 5:
        issues.append(f"starting_defenders:{lineup_shape['DEF']}")
    if not 2 <= lineup_shape["MID"] <= 5:
        issues.append(f"starting_midfielders:{lineup_shape['MID']}")
    if not 1 <= lineup_shape["FWD"] <= 3:
        issues.append(f"starting_forwards:{lineup_shape['FWD']}")

    if sum(1 for pick in squad if pick.get("is_captain")) != 1:
        issues.append("captain_count")
    if sum(1 for pick in squad if pick.get("is_vice_captain")) != 1:
        issues.append("vice_captain_count")
    return issues


def snapshot_quality(snapshot: dict[str, Any]) -> tuple[str, list[str]]:
    issues: list[str] = []
    managers = snapshot.get("competitors") or []
    if not managers:
        issues.append("competitors_empty")
    try:
        declared = int(snapshot.get("total_entries") or len(managers))
    except (TypeError, ValueError):
        declared = len(managers)
        issues.append(f"total_entries_non_numeric:{snapshot.get('total_entries')!r}")
    if len(managers) != declared:
        issues.append(f"hydration:{len(managers)}/{declared}")
    if snapshot.get("errors"):
        issues.append(f"collector_errors:{len(snapshot['errors'])}")
    for manager in managers:
        if not isinstance(manager, dict):
            issues.append("competitor_malformed")
            continue
        for issue in validate_manager_squad(manager):
            issues.append(f"entry:{manager.get('entry_id', 'unknown')}:{issue}")
            if len(issues) >= 25:
                issues.append("additional_issues_truncated")
                return "invalid", issues
    return ("valid" if not issues else "invalid"), issues
