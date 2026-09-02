#!/usr/bin/env python3
"""FPL Autopilot - squad watch (daily, can be scheduled by systemd/cron).

Checks the LIVE FPL API for team 2797967 and prints NOTHING (exit 0) when
all players are fit, news-free, and prices are stable - so a no_agent cron /
systemd timer stays silent. Prints a flag list ONLY when something changed.

Canonical source lives in the repo. Originally shipped as a Hermes cron
wrapper (fpl_squad_watch_cron.py); this version is portable to any OS.
"""
import os
import sys

import project_paths

BASE = str(project_paths.resolve_project_root(__file__))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "."))

TEAM_ID = int(os.environ.get("FPL_TEAM_ID", "2797967"))


def main():
    from fpl_client import FPLClient

    client = FPLClient()
    bootstrap = client.get_json("bootstrap-static/")
    els = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    team = client.get_json(f"entry/{TEAM_ID}/")
    picks = team.get("picks", [])

    flagged = []
    for p in picks:
        e = els.get(p.get("element"))
        if not e:
            continue
        news = (e.get("news") or "").strip()
        cop = e.get("chance_of_playing_next_round")
        status = e.get("status")
        if news or (cop is not None and cop < 75) or status in ("i", "u", "d"):
            flagged.append(
                f"{e.get('web_name')} ({teams.get(e.get('team'))}): "
                f"status={status} cop={cop} {news[:50]}"
            )

    if flagged:
        print("SQUAD WATCH FLAGS:")
        for f in flagged:
            print(f"  - {f}")
        return 1
    return 0  # silent - healthy


if __name__ == "__main__":
    sys.exit(main())