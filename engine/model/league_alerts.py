"""High-signal Telegram alerts derived from consecutive league snapshots.

The detector is pure and intentionally conservative: routine refreshes stay
silent, while prize posture, final registry, major late-entry, trusted-pick,
and captain-consensus changes can wake the owner.
"""

import hashlib
import json


def _mode(state):
    return str(((state or {}).get("mode") or {}).get("mode") or "Neutral")


def _members(state):
    return sum(int(row.get("member_count", 0) or 0) for row in (state or {}).get("leagues", []) or [])


def _captain_leader(state):
    rows = list(((state or {}).get("player_exposure") or {}).values())
    if not rows:
        return None
    leader = max(rows, key=lambda row: float(row.get("captain_share", 0) or 0))
    share = float(leader.get("captain_share", 0) or 0)
    if share < 50:
        return None
    return str(leader.get("name") or leader.get("element")), share


def meaningful_league_alerts(previous, current):
    """Return owner-worthy messages; an initial snapshot never creates noise."""
    if not isinstance(previous, dict) or not previous or not isinstance(current, dict):
        return []
    alerts = []
    old_registry = (previous.get("registry") or {}).get("status")
    new_registry = (current.get("registry") or {}).get("status")
    if old_registry != "final" and new_registry == "final":
        alerts.append(
            f"🔒 GW{current.get('event')} league registry FINAL: "
            f"{_members(current):,} memberships frozen."
        )

    delta = _members(current) - _members(previous)
    if new_registry != "final" and delta >= 10:
        alerts.append(
            f"👥 {delta} new league memberships detected before deadline "
            f"({_members(current):,} total)."
        )

    old_mode, new_mode = _mode(previous), _mode(current)
    if old_mode != new_mode:
        reason = str((current.get("mode") or {}).get("reason") or "prize position changed")
        alerts.append(f"⚔️ GW{current.get('event')} strategy changed {old_mode} → {new_mode}: {reason}")

    old_trusted = int(previous.get("trusted_pick_count", 0) or 0)
    new_trusted = int(current.get("trusted_pick_count", 0) or 0)
    cohort = int(current.get("cohort_count", 0) or 0)
    threshold = max(5, int(cohort * 0.75))
    if old_trusted < threshold <= new_trusted:
        alerts.append(f"✅ GW{current.get('event')} rival picks trusted: {new_trusted}/{cohort}; transfer and captain radar unlocked.")

    old_cap, new_cap = _captain_leader(previous), _captain_leader(current)
    if new_cap and old_cap != new_cap and new_trusted >= threshold:
        alerts.append(f"👑 GW{current.get('event')} sharp captain consensus: {new_cap[0]} {new_cap[1]:.1f}%.")
    return alerts


def alert_signature(alerts):
    payload = json.dumps(list(alerts or []), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
