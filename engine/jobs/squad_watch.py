#!/usr/bin/env python3
"""FPL Autopilot - squad availability watch (hourly, de-duplicated).

Checks the availability of every player you OWN (live my-team, so it includes
pending transfers) plus any player your pending plan wants to bring IN. Emits a
grouped flag list ONLY for players whose status / chance-of-playing / news
CHANGED since the last run - so an hourly systemd timer stays silent unless
something actually moved. Prints nothing (exit 0) when nothing changed.

Replaces the old daily version, which read ``entry/{id}/`` (no ``picks`` key)
and therefore never flagged anything.
"""
import hashlib
import json
import os
import sys

import project_paths

BASE = str(project_paths.resolve_project_root(__file__))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "."))

TEAM_ID = int(os.environ.get("FPL_TEAM_ID", "2797967"))
STATE_FILE = os.path.join(BASE, "data", "processed", "squad_watch_state.json")
PLAN_FILE = os.path.join(BASE, "data", "processed", "pending_plan.json")
_HARD = {"i": "injured", "u": "unavailable", "n": "not in squad", "s": "suspended"}


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _owned_and_incoming(client, bootstrap):
    """(owned element ids -> role, incoming element ids from the pending plan)."""
    events = bootstrap.get("events") or []
    last_finished = max((e["id"] for e in events if e.get("finished")), default=1)
    roles = {}
    picks = []
    try:
        picks = client.my_team(TEAM_ID).get("picks") or []
    except Exception:
        picks = []
    if not picks:
        for gw in (last_finished, last_finished - 1):
            if gw < 1:
                continue
            try:
                picks = client.get_json(f"entry/{TEAM_ID}/event/{gw}/picks/").get("picks") or []
            except Exception:
                continue
            if picks:
                break
    for pick in picks:
        eid = pick.get("element")
        if pick.get("is_captain"):
            roles[eid] = "(C)"
        elif pick.get("is_vice_captain"):
            roles[eid] = "(V)"
        elif pick.get("multiplier", 0) > 0 or pick.get("position", 99) <= 11:
            roles[eid] = "XI"
        else:
            roles[eid] = "bench"

    incoming = set()
    plan = _load_json(PLAN_FILE) or {}
    if plan.get("status") == "pending":
        for row in plan.get("target_starters") or []:
            if row.get("id"):
                incoming.add(int(row["id"]))
        for transfer in plan.get("transfers") or []:
            if transfer.get("element_in"):
                incoming.add(int(transfer["element_in"]))
    incoming -= set(roles)
    return roles, incoming


def _flag(element):
    status = element.get("status") or "a"
    cop = element.get("chance_of_playing_next_round")
    news = (element.get("news") or "").strip()
    if status in _HARD:
        return "hard", f"{_HARD[status]}" + (f" - {news[:70]}" if news else "")
    if (cop is not None and cop < 75) or status == "d":
        pct = f"{cop}%" if cop is not None else "doubt"
        return "soft", f"{pct} to play" + (f" - {news[:60]}" if news else "")
    if news:
        return "news", news[:80]
    return None, ""


def main():
    from fpl_client import FPLClient

    client = FPLClient()
    bootstrap = client.get_json("bootstrap-static/")
    els = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    roles, incoming = _owned_and_incoming(client, bootstrap)
    watch = {**{e: roles[e] for e in roles}, **{e: "→ incoming" for e in incoming}}

    previous = _load_json(STATE_FILE) or {}
    current = {}
    changed = {"hard": [], "soft": [], "news": [], "cleared": []}

    for eid, role in watch.items():
        element = els.get(eid)
        if not element:
            continue
        severity, detail = _flag(element)
        sig = ""
        if severity:
            sig = hashlib.sha1(  # noqa: S324 - not security, just a change key
                f"{severity}|{detail}".encode()).hexdigest()[:12]
            current[str(eid)] = sig
        name = f"{element.get('web_name')} ({teams.get(element.get('team'), '?')}) {role}".strip()
        if severity and previous.get(str(eid)) != sig:
            changed[severity].append(f"{name}: {detail}")
        elif not severity and str(eid) in previous:
            changed["cleared"].append(name)

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(current, handle)

    if not any(changed.values()):
        return 0

    lines = ["SQUAD WATCH - availability changes"]
    for key, head in (("hard", "OUT / suspended"), ("soft", "Doubtful"),
                      ("news", "News"), ("cleared", "Cleared - now fine")):
        if changed[key]:
            lines.append(f"\n{head}:")
            lines.extend(f"  - {row}" for row in changed[key])
    print("\n".join(lines))
    return 1


if __name__ == "__main__":
    sys.exit(main())
