"""Freshness metadata for the VM's published live league snapshots."""
from datetime import datetime, timezone


# The timer intentionally rests 23:30–07:00 UTC. Twelve hours tolerates that
# overnight gap, but catches a stopped collector. This is not a live-score SLA.
MAX_AGE_HOURS = 12


def snapshot_freshness(captured_at, *, now=None):
    now = now or datetime.now(timezone.utc)
    age = None
    try:
        captured = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
        if captured.tzinfo is not None:
            age = (now - captured).total_seconds() / 3600
    except (ValueError, TypeError):
        pass
    return {
        "freshness_hours": round(age, 2) if age is not None else None,
        "stale": age is None or age < -5 / 60 or age > MAX_AGE_HOURS,
        "max_age_hours": MAX_AGE_HOURS,
    }
