"""Bonus layer for the live v2 pipeline (Sol directive P5).

Replacement-style additive correction, NOT stacking:
    embedded_bonus = historical bonus already inside v2's form/baseline rate
    bonus_delta    = E[bonus]_2026 (fixture simulation, unconditional) - embedded
    v2_live        = v2_total + bonus_delta

Rules:
- E[bonus] from data/processed/gw{n}_bonus.json is UNCONDITIONAL (minutes
  uncertainty already inside), so we never re-multiply by play_prob or
  odds_multiplier (Sol directive point 6).
- Embedded bonus estimate per position uses the 2025-26 observed mean bonus
  per 90 (DEF 0.183, MID 0.296, FWD 0.829, GKP 0.211) scaled by the same
  play-probability factor the v2 rate uses. This is the "remove the average
  bonus that v2's total-points-derived rate already carries" step.
- Feature flag settings.json `bonus_model_enabled` (default false per Sol:
  the ML row-wise model failed the 5% gate and stays diagnostics-only; the
  fixture-sim rule-delta layer is the one quantified by official 2026/27
  coefficients and can be enabled deliberately).
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 2025-26 observed mean bonus per 90 by position (from vaastav gws data)
EMBEDDED_BONUS90 = {"GKP": 0.211, "DEF": 0.183, "MID": 0.296, "FWD": 0.829}

BONUS_FILE = os.path.join(BASE, "data", "processed", "gw_bonus.json")


def load_bonus_file(gw=None):
    """Load the fixture-sim E[bonus] map. Returns {element_id: {...}} or {}."""
    path = BONUS_FILE
    if gw is not None:
        alt = os.path.join(BASE, "data", "processed", f"gw{gw}_bonus.json")
        if os.path.exists(alt):
            path = alt
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("players", data if isinstance(data, dict) and "players" not in data else {})
    except Exception:
        return {}


def embedded_bonus(position, mp):
    """Historical bonus already inside the v2 rate for this player."""
    return EMBEDDED_BONUS90.get(position, 0.25) * mp


def bonus_delta(element_id, position, mp, bonus_map):
    """Δbonus = E[bonus]_2026 − embedded. Bounded to [-2, +2] to avoid
    a single fixture-sim artifact dominating the projection."""
    rec = bonus_map.get(str(element_id)) or bonus_map.get(element_id)
    if not rec:
        return 0.0
    e_bonus = float(rec.get("e_bonus", 0.0) or 0.0)
    emb = embedded_bonus(position, mp)
    return max(-2.0, min(2.0, e_bonus - emb))


def apply_bonus(player, mp, bonus_map, enabled=True):
    """Return a COPY of the player dict with xpts adjusted by bonus_delta."""
    if not enabled:
        return player
    d = dict(player)
    delta = bonus_delta(d.get("id"), d.get("position"), mp, bonus_map)
    if delta:
        d = dict(d)
        d["xpts"] = max(0.0, float(d.get("xpts", 0.0)) + delta)
        d["xpts_horizon"] = max(0.0, float(d.get("xpts_horizon", 0.0)) + delta)
        d["bonus_delta"] = round(delta, 3)
        d["e_bonus"] = round(bonus_map.get(str(d["id"]), {}).get("e_bonus", 0.0), 3)
    return d
