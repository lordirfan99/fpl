"""Prize-boundary calculations for target leagues."""

from __future__ import annotations

import json
import os


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(BASE, "config", "prize_targets.json")


def load_prize_config(path=None):
    try:
        with open(path or DEFAULT_CONFIG, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {"leagues": []}


def band_for_rank(bands, rank):
    if rank is None:
        return None
    rank = int(rank)
    return next((dict(b) for b in bands if int(b["rank_from"]) <= rank <= int(b["rank_to"])), None)


def next_better_band(bands, rank):
    if rank is None:
        return None
    rank = int(rank)
    candidates = [b for b in bands if int(b["rank_to"]) < rank]
    return dict(max(candidates, key=lambda b: int(b["rank_to"]))) if candidates else None


def points_at_rank(rows, rank):
    ranked = [r for r in rows if r.get("rank") is not None and r.get("total") is not None]
    if not ranked:
        return None
    exact = [r for r in ranked if int(r["rank"]) <= int(rank)]
    if not exact:
        return None
    boundary = max(exact, key=lambda r: int(r["rank"]))
    return float(boundary["total"])


def calculate_prize_status(rows, our_entry, league_config, completed_gws=0):
    league_id = int(league_config["league_id"])
    league_rows = [r for r in rows if int(r.get("league_id", -1)) == league_id]
    ours = next((r for r in league_rows if int(r.get("entry", -1)) == int(our_entry)), None)
    base = {
        "league_id": league_id,
        "league_name": league_config.get("name"),
        "priority": int(league_config.get("priority", 99)),
        "rank": None,
        "points": None,
        "current_prize": None,
        "next_target": None,
        "gap_to_next_target": None,
        "drop_buffer": None,
        "remaining_gws": max(1, 38 - int(completed_gws or 0)),
    }
    if not ours or ours.get("rank") is None or ours.get("total") is None:
        return base
    rank = int(ours["rank"])
    points = float(ours["total"])
    bands = league_config.get("overall", []) or []
    current = band_for_rank(bands, rank)
    better = next_better_band(bands, rank)
    base.update({"rank": rank, "points": points, "current_prize": current, "next_target": better})
    if better:
        boundary_points = points_at_rank(league_rows, int(better["rank_to"]))
        if boundary_points is not None:
            base["gap_to_next_target"] = max(0.0, boundary_points - points + 1.0)
    if current:
        outside_points = points_at_rank(league_rows, int(current["rank_to"]) + 1)
        if outside_points is not None:
            base["drop_buffer"] = max(0.0, points - outside_points)
    return base


def prize_mode(statuses, completed_gws):
    """Choose mode from the highest-priority league with live standings."""
    active = sorted((s for s in statuses if s.get("rank") is not None), key=lambda s: s.get("priority", 99))
    remaining = max(1, 38 - int(completed_gws or 0))
    if not active:
        return {"mode": "Neutral", "remaining_gws": remaining, "reason": "Prize standings are not available yet.", "target": None}
    target = active[0]
    if remaining > 10 or int(completed_gws or 0) < 4:
        return {"mode": "Neutral", "remaining_gws": remaining,
                "reason": "Early/mid-season: maximize expected points before activating prize-boundary variance.",
                "target": target}
    gap = target.get("gap_to_next_target")
    buffer = target.get("drop_buffer")
    if gap is not None and gap / remaining > 1.0:
        return {"mode": "Chase", "remaining_gws": remaining,
                "required_swing_per_gw": round(gap / remaining, 2),
                "reason": "The primary prize boundary requires more than one point gained per remaining GW.",
                "target": target}
    if target.get("current_prize") and buffer is not None and buffer / remaining <= 2.0:
        return {"mode": "Protect", "remaining_gws": remaining,
                "required_swing_per_gw": 0.0,
                "reason": "We occupy a prize band with a narrow points buffer.",
                "target": target}
    return {"mode": "Neutral", "remaining_gws": remaining,
            "required_swing_per_gw": round((gap or 0.0) / remaining, 2),
            "reason": "The prize gap is reachable without sacrificing expected points.",
            "target": target}
