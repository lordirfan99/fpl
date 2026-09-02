"""Deadline-valid rolling features for the Competitive V4.2 shadow model.

Only official FPL payloads are accepted.  Event rows are appended after FPL
marks a gameweek finished and data-checked; callers always filter ``gw`` to be
strictly less than the gameweek being predicted.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
import os
import tempfile


EVENT_FIELDS = (
    "minutes", "starts", "goals_scored", "assists", "expected_goals",
    "expected_assists", "clean_sheets", "goals_conceded", "saves", "bonus",
    "yellow_cards", "red_cards", "defensive_contribution", "total_points",
)


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_event_history(path):
    """Load the append-only JSONL store, ignoring a torn final line safely."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(row, dict) and row.get("gw") and row.get("element"):
                rows.append(row)
    return rows


def write_event_rows(path, rows):
    """Idempotently merge official event rows using an atomic replacement."""
    existing = load_event_history(path)
    merged = {(int(r["gw"]), int(r["element"])): r for r in existing}
    for row in rows:
        merged[(int(row["gw"]), int(row["element"]))] = row
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="v42-history-", suffix=".jsonl",
                               dir=os.path.dirname(path), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for key in sorted(merged):
                handle.write(json.dumps(merged[key], sort_keys=True) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def event_rows(gw, live_payload, elements, captured_at):
    """Normalize an official ``event/{gw}/live`` payload for the feature store."""
    meta = {int(e["id"]): e for e in elements}
    rows = []
    for item in live_payload.get("elements", []):
        element = int(item.get("id") or 0)
        if not element:
            continue
        stats = item.get("stats") or {}
        row = {
            "gw": int(gw), "element": element,
            "team": int((meta.get(element) or {}).get("team") or 0),
            "position": int((meta.get(element) or {}).get("element_type") or 0),
            "captured_at": captured_at,
        }
        for field in EVENT_FIELDS:
            row[field] = stats.get(field, 0)
        rows.append(row)
    return rows


def history_by_player(rows, target_gw):
    out = defaultdict(list)
    for row in rows:
        if int(row.get("gw") or 0) < int(target_gw):
            out[int(row["element"])].append(row)
    for values in out.values():
        values.sort(key=lambda r: int(r["gw"]))
    return dict(out)


def team_rotation_rate(history, team, lookback=5):
    """Estimate recent starting-XI turnover from strictly lagged event rows."""
    starters = defaultdict(set)
    all_rows = [row for rows in history.values() for row in rows
                if int(row.get("team") or 0) == int(team)]
    recent_gws = sorted({int(row["gw"]) for row in all_rows})[-lookback:]
    for row in all_rows:
        gw = int(row["gw"])
        if gw not in recent_gws:
            continue
        if _f(row.get("starts")) > 0 or _f(row.get("minutes")) >= 60:
            starters[gw].add(int(row["element"]))
    changes = []
    for left, right in zip(recent_gws, recent_gws[1:]):
        if starters[left] and starters[right]:
            retained = len(starters[left] & starters[right])
            changes.append(1.0 - retained / max(1.0, min(len(starters[left]),
                                                        len(starters[right]))))
    return max(0.0, min(0.25, sum(changes) / len(changes))) if changes else 0.0


def player_rates(rows, prior, prior_minutes=900.0):
    """Exposure-shrunk, strictly lagged per-90 component rates."""
    minutes = sum(_f(r.get("minutes")) for r in rows)
    result = {"sample_minutes": minutes, "sample_gws": len(rows)}
    for field, base in prior.items():
        observed = 90.0 * sum(_f(r.get(field)) for r in rows) / max(90.0, minutes)
        result[field] = ((observed * minutes) + (float(base) * prior_minutes)) / (
            minutes + prior_minutes
        )
    return result


def team_strengths(fixtures, target_gw):
    """Return lagged attack/defence factors derived only from completed fixtures."""
    played = [f for f in fixtures if f.get("finished") and f.get("event")
              and int(f["event"]) < int(target_gw)
              and f.get("team_h_score") is not None and f.get("team_a_score") is not None]
    totals = defaultdict(lambda: {"gf": 0.0, "ga": 0.0, "n": 0.0})
    for fixture in played:
        home, away = int(fixture["team_h"]), int(fixture["team_a"])
        hg, ag = _f(fixture["team_h_score"]), _f(fixture["team_a_score"])
        totals[home]["gf"] += hg
        totals[home]["ga"] += ag
        totals[home]["n"] += 1
        totals[away]["gf"] += ag
        totals[away]["ga"] += hg
        totals[away]["n"] += 1
    league_goals = sum(v["gf"] for v in totals.values())
    league_games = sum(v["n"] for v in totals.values())
    league_rate = league_goals / max(1.0, league_games)
    output = {}
    for team, values in totals.items():
        # Six team-games of prior evidence stop GW1/GW2 scorelines dominating.
        attack = (values["gf"] + 6.0 * league_rate) / (values["n"] + 6.0)
        conceded = (values["ga"] + 6.0 * league_rate) / (values["n"] + 6.0)
        output[team] = {
            "attack": max(0.70, min(1.35, attack / max(0.25, league_rate))),
            "defence_weakness": max(0.70, min(1.35, conceded / max(0.25, league_rate))),
            "matches": int(values["n"]),
        }
    return output


def fixture_factors(team, fixture, strengths):
    own = strengths.get(int(team), {})
    opponent = strengths.get(int(fixture.get("opponent") or 0), {})
    venue = 1.06 if fixture.get("home") else 0.96
    attack = venue * _f(own.get("attack"), 1.0) * _f(opponent.get("defence_weakness"), 1.0)
    opponent_attack = (1.0 / venue) * _f(opponent.get("attack"), 1.0)
    own_weakness = _f(own.get("defence_weakness"), 1.0)
    clean_sheet = math.exp(-1.20 * opponent_attack * own_weakness)
    return {
        "attack": max(0.65, min(1.45, attack)),
        "clean_sheet": max(0.05, min(0.62, clean_sheet)),
        "evidence_matches": min(int(own.get("matches", 0)),
                                int(opponent.get("matches", 0))),
    }
