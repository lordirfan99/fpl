#!/usr/bin/env python3
"""FPL Autopilot - pre-deadline lineup guard (hourly timer, self-gating).

Runs every hour but only speaks once, in the last few hours before a GW
deadline, with a single checklist card:

  * your XI + captain as they stand right now (live my-team, includes any
    pending transfers);
  * red flags - injured / suspended / doubtful starters, price-locked news;
  * bench-order and captain sanity vs the latest projection;
  * a reminder if a pending plan is still unapproved.

Silent (exit 0) outside the warning window or once it has already fired for
the upcoming GW. Read-only: it never changes the team.
"""
import datetime
import json
import os
import sys

import project_paths

BASE = str(project_paths.resolve_project_root(__file__))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "."))

from fpl_client import FPLClient

TEAM_ID = int(os.environ.get("FPL_TEAM_ID", "2797967"))
STATE_FILE = os.path.join(BASE, "data", "processed", "deadline_check_state.json")
PLAN_FILE = os.path.join(BASE, "data", "processed", "pending_plan.json")
WINDOW_LO_MIN = 60
WINDOW_HI_MIN = 210
POS_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
BENCH_EDGE = 1.0  # xPts a bench player must beat a same-position starter by


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _next_event(bootstrap):
    now = datetime.datetime.now(datetime.timezone.utc)
    for ev in bootstrap.get("events") or []:
        deadline = datetime.datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
        if not ev.get("finished") and deadline > now:
            return ev["id"], deadline
    return None, None


def _projection_xpts():
    folder = os.path.join(BASE, "data", "processed")
    if not os.path.isdir(folder):
        return {}
    preds = sorted((p for p in os.listdir(folder)
                    if p.startswith("predictions_gw") and p.endswith(".json")),
                   key=lambda name: int("".join(filter(str.isdigit, name)) or 0))
    if not preds:
        return {}
    data = _load_json(os.path.join(folder, preds[-1])) or {}
    return {int(p["id"]): float(p.get("xpts") or 0)
            for p in data.get("players") or [] if p.get("id") is not None}


def _live_picks(client, event):
    try:
        team = client.my_team(TEAM_ID)
        if team.get("picks"):
            return team["picks"], team.get("transfers") or {}
    except Exception:
        pass
    for gw in (event - 1, event - 2):
        if gw < 1:
            continue
        try:
            picks = client.get_json(f"entry/{TEAM_ID}/event/{gw}/picks/").get("picks") or []
        except Exception:
            continue
        if picks:
            return picks, {}
    return [], {}


def build_report(client):
    bootstrap = client.get_json("bootstrap-static/")
    els = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    event, deadline = _next_event(bootstrap)
    if event is None:
        return None, None

    mins_left = (deadline - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 60
    if not WINDOW_LO_MIN <= mins_left <= WINDOW_HI_MIN:
        return None, None
    if (_load_json(STATE_FILE) or {}).get("fired_event") == event:
        return None, None

    picks, transfers = _live_picks(client, event)
    if not picks:
        return None, None
    xpts = _projection_xpts()

    def _row(pick):
        e = els.get(pick["element"], {})
        return {
            "id": pick["element"],
            "name": e.get("web_name", str(pick["element"])),
            "team": teams.get(e.get("team"), "?"),
            "pos": POS_MAP.get(e.get("element_type"), "?"),
            "slot": pick.get("position", 0),
            "starter": pick.get("multiplier", 0) > 0 or pick.get("position", 99) <= 11,
            "is_captain": pick.get("is_captain"),
            "is_vice": pick.get("is_vice_captain"),
            "status": e.get("status", "a"),
            "cop": e.get("chance_of_playing_next_round"),
            "news": (e.get("news") or "").strip(),
            "xp": xpts.get(pick["element"], float(e.get("ep_next") or 0)),
        }

    rows = sorted((_row(p) for p in picks), key=lambda r: r["slot"])
    starters = [r for r in rows if r["starter"]]
    bench = [r for r in rows if not r["starter"]]

    hrs, mins = divmod(int(mins_left), 60)
    lines = [f"DEADLINE CHECK · GW{event} in {hrs}h{mins:02d}m",
             f"({deadline.strftime('%a %d %b %H:%M')} UTC)", ""]

    lines.append("Starting XI:")
    for r in starters:
        role = " (C)" if r["is_captain"] else (" (V)" if r["is_vice"] else "")
        lines.append(f"  {r['pos']:<3} {r['name']:<15} {r['team']:<3} xP {r['xp']:>4.1f}{role}")
    lines.append("Bench:")
    for i, r in enumerate(bench, 1):
        lines.append(f"  {i}. {r['pos']:<3} {r['name']:<15} {r['team']:<3} xP {r['xp']:>4.1f}")

    # --- red flags on starters ------------------------------------------
    flags = []
    for r in starters:
        reason = None
        if r["status"] in ("i", "u", "n", "s"):
            reason = {"i": "injured", "u": "unavailable", "n": "not in squad",
                      "s": "suspended"}[r["status"]]
        elif r["cop"] is not None and r["cop"] < 100:
            reason = f"{r['cop']}% to play"
        elif r["news"]:
            reason = r["news"][:60]
        if reason:
            flags.append(f"  ⚠️ {r['name']} ({r['team']}): {reason}")
    if flags:
        lines.append("\nRed flags:")
        lines.extend(flags)

    # --- bench-order sanity vs projection ------------------------------
    if xpts:
        swaps = []
        for b in bench:
            for s in starters:
                if s["pos"] == b["pos"] and b["xp"] - s["xp"] > BENCH_EDGE:
                    swaps.append(f"  ↕ start {b['name']} ({b['xp']:.1f}) over "
                                 f"{s['name']} ({s['xp']:.1f})")
                    break
        if swaps:
            lines.append("\nLineup tweaks to consider:")
            lines.extend(swaps)

        cap = next((r for r in starters if r["is_captain"]), None)
        best = max(starters, key=lambda r: r["xp"], default=None)
        if cap and best and best["id"] != cap["id"] and best["xp"] - cap["xp"] > 0.5:
            lines.append(f"\n👑 Captain: on {cap['name']} ({cap['xp']:.1f}); "
                         f"top projected is {best['name']} ({best['xp']:.1f}).")

    # --- unapproved plan reminder ------------------------------------
    plan = _load_json(PLAN_FILE) or {}
    if plan.get("status") == "pending":
        n = len(plan.get("transfers") or [])
        lines.append(f"\n🔔 A pending plan for GW{plan.get('gw', event)} "
                     f"({n} transfer{'s' if n != 1 else ''}) is NOT approved — "
                     f"open the bot and /approve, or it will not run.")
    elif transfers.get("status") != "unlimited" and transfers.get("limit") is not None:
        free = max(0, (transfers.get("limit") or 0) - (transfers.get("made") or 0))
        lines.append(f"\nℹ️ {free} free transfer(s) available, no plan staged.")

    return "\n".join(lines), event


def main():
    try:
        report, event = build_report(FPLClient())
    except Exception as exc:  # noqa: BLE001 - a guard job must never crash its timer
        print(f"deadline check error: {exc!r}", file=sys.stderr)
        return 0
    if report:
        print(report)
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump({"fired_event": event,
                       "fired_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
                      handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
