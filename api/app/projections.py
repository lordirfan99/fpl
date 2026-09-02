"""Transparent, read-only V5 player projection laboratory.

This module deliberately has no dependency on league ownership, rank, or
captaincy.  It projects the complete official FPL player catalogue.
"""

from __future__ import annotations

from typing import Any

from .projection_types import ExpectedMinutes, PlayerProjection
from .scoring import (
    CLEAN_SHEET_POINTS, GOAL_POINTS, appearance_points, clamp,
    conceded_points, defensive_contribution_points, goalkeeper_save_points,
)


POSITION_BY_TYPE = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None and value != "" else default)
    except (TypeError, ValueError):
        return default


def expected_minutes(player: dict[str, Any]) -> ExpectedMinutes:
    status = str(player.get("status") or "a")
    chance = player.get("chance_of_playing_next_round")
    availability = clamp(number(chance, 100.0) / 100.0) if chance is not None else 1.0
    if status in {"u", "s"}:
        availability = 0.0
    minutes = max(0.0, number(player.get("minutes")))
    starts = max(0.0, number(player.get("starts")))
    appearances = max(starts, number(player.get("appearances")), minutes / 45.0)
    start_rate = starts / appearances if appearances else (0.82 if status == "a" else 0.35)
    p_start = clamp(start_rate * availability)
    p_bench = clamp((1.0 - p_start) * 0.30 * availability)
    minutes_if_start = clamp(minutes / starts if starts else 75.0, 45.0, 90.0)
    expected = p_start * minutes_if_start + p_bench * 22.0
    p_60_plus = clamp(p_start * clamp((minutes_if_start - 45.0) / 25.0))
    return ExpectedMinutes(
        p_start=round(p_start, 4), p_bench_appearance=round(p_bench, 4),
        minutes_if_start=round(minutes_if_start, 2), minutes_if_bench=22.0,
        expected_minutes=round(clamp(expected, 0.0, 90.0), 2), p_60_plus=round(p_60_plus, 4),
    )


def fixture_multipliers(
    player: dict[str, Any], fixtures: list[dict[str, Any]], teams: dict[int, str]
) -> list[tuple[float, str]]:
    team_id = player.get("team")
    team_name = teams.get(team_id)
    matches: list[tuple[float, str]] = []
    for fixture in fixtures:
        if fixture.get("team_h") in {team_id, team_name}:
            matches.append((clamp(1.12 - 0.08 * number(fixture.get("team_h_difficulty"), 3), 0.72, 1.12), "home"))
        elif fixture.get("team_a") in {team_id, team_name}:
            matches.append((clamp(1.08 - 0.08 * number(fixture.get("team_a_difficulty"), 3), 0.68, 1.08), "away"))
    return matches


def fixture_multiplier(
    player: dict[str, Any], fixtures: list[dict[str, Any]], teams: dict[int, str]
) -> tuple[float, str | None]:
    """Compatibility helper for callers that need only the first fixture."""
    matches = fixture_multipliers(player, fixtures, teams)
    return matches[0] if matches else (0.0, None)


def project_player(player: dict[str, Any], teams: dict[int, str], fixtures: list[dict[str, Any]]) -> PlayerProjection:
    position = POSITION_BY_TYPE.get(player.get("element_type"), "FWD")
    minutes = expected_minutes(player)
    matches = fixture_multipliers(player, fixtures, teams)
    scale = minutes.expected_minutes / 90.0
    xg90 = number(player.get("expected_goals_per_90"))
    xa90 = number(player.get("expected_assists_per_90"))
    if not xg90 and minutes.expected_minutes:
        xg90 = number(player.get("expected_goals")) / max(1.0, number(player.get("minutes"))) * 90
    if not xa90 and minutes.expected_minutes:
        xa90 = number(player.get("expected_assists")) / max(1.0, number(player.get("minutes"))) * 90
    xgc90 = number(player.get("expected_goals_conceded_per_90"))
    dc90 = number(player.get("defensive_contribution_per_90"))
    if not dc90:
        dc90 = number(player.get("defensive_contribution")) / max(1.0, number(player.get("minutes"))) * 90
    saves90 = number(player.get("saves_per_90"))
    if not saves90:
        saves90 = number(player.get("saves")) / max(1.0, number(player.get("minutes"))) * 90
    components = {key: 0.0 for key in (
        "appearance", "goals", "assists", "clean_sheet", "saves",
        "defensive_contribution", "goals_conceded", "bonus",
    )}
    expected_goals = expected_assists = 0.0
    for multiplier, _venue in matches:
        fixture_goals = xg90 * scale * multiplier
        fixture_assists = xa90 * scale * multiplier
        expected_goals += fixture_goals
        expected_assists += fixture_assists
        expected_conceded = max(0.0, xgc90 * scale / max(multiplier, 0.1))
        # Transparent monotonic heuristic pending an empirically calibrated
        # team clean-sheet model.  Easier attacking multipliers must never
        # reduce the clean-sheet term for an otherwise identical player.
        clean_sheet_probability = clamp(0.45 * minutes.p_60_plus * multiplier)
        components["appearance"] += appearance_points(minutes.expected_minutes, minutes.p_60_plus)
        components["goals"] += fixture_goals * GOAL_POINTS[position]
        components["assists"] += fixture_assists * 3.0
        components["clean_sheet"] += clean_sheet_probability * CLEAN_SHEET_POINTS[position]
        components["saves"] += goalkeeper_save_points(saves90 * scale) if position == "GKP" else 0.0
        components["defensive_contribution"] += defensive_contribution_points(position, dc90 * scale)
        components["goals_conceded"] += conceded_points(position, expected_conceded)
        components["bonus"] += number(player.get("bonus")) / max(1.0, number(player.get("minutes"))) * minutes.expected_minutes
    mean = max(0.0, sum(components.values()))
    # A deterministic, transparent uncertainty envelope pending calibrated simulation.
    spread = max(1.5, mean * 0.65) if matches else 0.0
    issues = []
    if not matches:
        issues.append("blank_gameweek:no_fixture")
    if not (xg90 or xa90):
        issues.append("underlying_attack_missing")
    return PlayerProjection(
        element=int(player["id"]), name=str(player.get("web_name") or player["id"]),
        team=teams.get(player.get("team"), "Unknown"), position=position,
        xpts_mean=round(mean, 2), p10=round(max(0.0, mean - spread), 2),
        p50=round(mean, 2), p90=round(mean + spread, 2),
        p_return=round(clamp(1.0 - __import__("math").exp(-max(0.0, expected_goals + expected_assists))), 4),
        p_10_plus=round(clamp((mean - 5.0) / 10.0), 4), expected_minutes=minutes,
        components={key: round(value, 3) for key, value in components.items()},
        source="official-fpl-bootstrap-v5-lab", quality_issues=tuple(issues),
    )


def build_projections(bootstrap: dict[str, Any], fixtures: list[dict[str, Any]]) -> list[PlayerProjection]:
    teams = {team["id"]: team.get("name", "Unknown") for team in bootstrap.get("teams", [])}
    players = [player for player in bootstrap.get("elements", []) if player.get("id")]
    return sorted((project_player(player, teams, fixtures) for player in players), key=lambda row: row.xpts_mean, reverse=True)
