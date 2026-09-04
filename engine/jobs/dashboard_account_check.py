"""Lightweight account verification; never optimizes, notifies or writes to FPL."""
import datetime
import json
import sys
from pathlib import Path

from project_paths import resolve_project_root

BASE = resolve_project_root(__file__)
sys.path.insert(0, str(BASE / "model"))
sys.path.insert(0, str(BASE / "execution"))
from dashboard_packet import account_fingerprint, private_bucket, publish  # noqa: E402
from fpl_client import FPLClient  # noqa: E402


def check(base, client, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    settings = json.loads((Path(base) / "config" / "settings.json").read_text())
    result = {"schema_version": 1, "team_id": settings["team_id"], "checked_at": now.isoformat(),
              "verified": False, "account_fingerprint": None}
    try:
        result["account_fingerprint"] = account_fingerprint(client.my_team(settings["team_id"]))
        plan = json.loads((Path(base) / "data" / "processed" / "pending_plan.json").read_text())
        result["plan_id"] = plan.get("plan_id")
        result["verified"] = True
    except Exception:
        # Publish invalidation, not a fresh timestamp on the old successful check.
        pass
    publish(base, "account-check", result)
    return result["verified"]


if __name__ == "__main__":
    if not private_bucket(BASE):
        raise SystemExit("Private dashboard not configured")
    raise SystemExit(0 if check(BASE, FPLClient()) else 1)
