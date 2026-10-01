"""Advisory season-long chip plan.

Pure and read-only. It scans the visible fixture horizon for double and blank
gameweeks and suggests a target GW for each chip the owner still holds. It is
never merged into the executable plan, never consumes competitive context, and
carries no execution authority - the owner stages a chip and regenerates a
fresh canonical plan before approval, exactly as with chip_advisor.
"""
from __future__ import annotations

from typing import Any, Iterable

from chip_advisor import detect_bgw, detect_dgw
from chip_strategy import CHIP_PRIOR

STANDARD_CHIPS = ("wildcard", "freehit", "bboost", "3xc")
CHIP_LABEL = {
    "wildcard": "Wildcard", "freehit": "Free Hit",
    "bboost": "Bench Boost", "3xc": "Triple Captain",
}


def _swing_scan(fixtures: list[dict], gameweeks: list[int], n_teams: int) -> list[dict[str, Any]]:
    rows = []
    for gameweek in gameweeks:
        rows.append({
            "gw": gameweek,
            "dgw_teams": sorted(detect_dgw(fixtures, gameweek)),
            "bgw_teams": sorted(detect_bgw(fixtures, gameweek, n_teams)),
        })
    return rows


def _ranked(rows: list[dict[str, Any]], key: str, owned: set[int]) -> list[tuple[int, list[int], int]]:
    """(gw, teams, squad_overlap) for weeks with the swing, best first."""
    out = [
        (row["gw"], row[key], len(owned & set(row[key])) if owned else len(row[key]))
        for row in rows if row[key]
    ]
    out.sort(key=lambda item: (-item[2], item[0]))
    return out


def season_chip_plan(
    fixtures: list[dict],
    current_gw: int,
    *,
    available_chips: Iterable[str],
    squad_team_ids: Iterable[int] | None = None,
    n_teams: int = 20,
    last_gw: int = 38,
) -> list[dict[str, Any]]:
    """Return an advisory {chip, target_gw, reason, confidence} per held chip."""
    gameweeks = list(range(max(1, current_gw), last_gw + 1))
    scan = _swing_scan(fixtures, gameweeks, n_teams)
    owned = {int(team) for team in (squad_team_ids or []) if team is not None}
    doubles = _ranked(scan, "dgw_teams", owned)
    blanks = _ranked(scan, "bgw_teams", owned)

    plan: list[dict[str, Any]] = []
    for chip in sorted(set(available_chips), key=lambda code: -CHIP_PRIOR.get(code, 0.0)):
        entry: dict[str, Any] = {"chip": chip, "chip_label": CHIP_LABEL.get(chip, chip),
                                 "target_gw": None, "confidence": "low",
                                 "reason": "No double or blank gameweek is visible in the fixture horizon yet."}
        if chip in ("bboost", "3xc") and doubles:
            gw, teams, overlap = doubles[0]
            threshold = 5 if chip == "bboost" else 1
            entry.update(target_gw=gw,
                         confidence="medium" if overlap >= threshold else "low",
                         reason=(f"GW{gw} is the biggest double gameweek in view "
                                 f"({len(teams)} teams"
                                 + (f", {overlap} in your squad" if owned else "") + ")."))
        elif chip == "freehit" and blanks:
            gw, teams, overlap = blanks[0]
            entry.update(target_gw=gw,
                         confidence="medium" if overlap >= 4 else "low",
                         reason=(f"GW{gw} is the largest blank ({len(teams)} teams"
                                 + (f", {overlap} of yours" if owned else "")
                                 + "); Free Hit fields a full XI without transfers."))
        elif chip == "wildcard":
            target = doubles[0][0] - 1 if doubles else None
            entry.update(target_gw=target, confidence="low",
                         reason=(f"Hold for a fixture swing; a natural window is the GW{target} "
                                 f"deadline, before the GW{doubles[0][0]} double."
                                 if target else
                                 "Hold for a fixture swing or an injury pile-up; no dated trigger "
                                 "in the current fixture horizon."))
        plan.append(entry)
    return plan
