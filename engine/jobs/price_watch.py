#!/usr/bin/env python3
"""FPL Autopilot - price-change watch (scheduled, silent unless it matters).

FPL prices move once a day (~01:30 UTC). Missing a fall on a player you own
bleeds team value; missing a rise on a target costs you 0.1m of budget. This
job runs twice a day and:

  * reports CONFIRMED overnight changes (diff of ``now_cost`` vs the last run),
    always for players you own or have on your shortlist;
  * flags players with strong transfer momentum that are likely to change at
    the next lock (approximate - FPL's exact threshold is not public).

Prints NOTHING (exit 0) when nothing you care about moved, so a systemd timer
piping this into deliver_stdout.py stays quiet. All reads are the public,
no-auth FPL API. It never writes to the FPL account.
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
STATE_FILE = os.path.join(BASE, "data", "processed", "price_watch_state.json")
LEAGUE_STATE_FILE = os.path.join(BASE, "data", "processed", "league_intelligence", "latest.json")
# Net transfers (in - out) this event past which FPL usually flags a change.
# Deliberately conservative: better to under-warn than cry wolf every run.
MOMENTUM_THRESHOLD = 45000


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _current_event(bootstrap):
    events = bootstrap.get("events") or []
    for ev in events:
        if ev.get("is_current"):
            return ev["id"]
    finished = [ev["id"] for ev in events if ev.get("finished")]
    return max(finished) if finished else 1


def _owned_ids(client, event):
    for gw in (event, event - 1, event - 2):
        if gw < 1:
            continue
        try:
            picks = client.get_json(f"entry/{TEAM_ID}/event/{gw}/picks/").get("picks") or []
        except Exception:
            continue
        ids = {p["element"] for p in picks}
        if ids:
            return ids
    return set()


def _target_ids(els_by_name):
    """Shortlist = rival sharp-money consensus + our own top projection."""
    targets = set()
    league = _load_json(LEAGUE_STATE_FILE) or {}
    for row in league.get("transfer_consensus") or []:
        eid = row.get("element")
        if eid:
            targets.add(int(eid))
            continue
        hit = els_by_name.get(str(row.get("name") or "").strip().lower())
        if hit:
            targets.add(hit)
    preds = sorted(
        (p for p in os.listdir(os.path.join(BASE, "data", "processed"))
         if p.startswith("predictions_gw") and p.endswith(".json")),
        key=lambda name: int("".join(filter(str.isdigit, name)) or 0),
    ) if os.path.isdir(os.path.join(BASE, "data", "processed")) else []
    if preds:
        latest = _load_json(os.path.join(BASE, "data", "processed", preds[-1])) or {}
        ranked = sorted((latest.get("players") or []),
                        key=lambda p: -(p.get("xpts") or 0))[:15]
        for p in ranked:
            if p.get("id"):
                targets.add(int(p["id"]))
    return targets


def _price(cost):
    return f"£{cost / 10:.1f}m"


def build_report(client):
    bootstrap = client.get_json("bootstrap-static/")
    els = {e["id"]: e for e in bootstrap["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    els_by_name = {}
    for e in els.values():
        for key in (e.get("web_name"), f"{e.get('first_name','')} {e.get('second_name','')}".strip()):
            if key:
                els_by_name.setdefault(key.strip().lower(), e["id"])

    event = _current_event(bootstrap)
    owned = _owned_ids(client, event)
    targets = _target_ids(els_by_name) - owned

    prev = _load_json(STATE_FILE) or {}
    prev_prices = {int(k): v for k, v in (prev.get("prices") or {}).items()}
    now_prices = {eid: e["now_cost"] for eid, e in els.items()}

    # --- confirmed changes since the last run -------------------------------
    changed = []
    for eid, cost in now_prices.items():
        old = prev_prices.get(eid)
        if old is not None and old != cost:
            changed.append((eid, cost - old))

    def _tag(eid):
        return "🏠" if eid in owned else ("🎯" if eid in targets else "")

    def _fmt(eid, delta):
        e = els[eid]
        arrow = "🔺" if delta > 0 else "🔻"
        mark = _tag(eid)
        mark = f"{mark} " if mark else ""
        return (f"{arrow} {mark}{e['web_name']} ({teams.get(e['team'], '?')}) "
                f"{_price(e['now_cost'])} ({delta / 10:+.1f})")

    owned_changed = [c for c in changed if c[0] in owned]
    target_changed = [c for c in changed if c[0] in targets]
    other_changed = [c for c in changed if c[0] not in owned and c[0] not in targets]

    # --- momentum: likely to change at the next lock ----------------------
    momentum = []
    for eid, e in els.items():
        if any(c[0] == eid for c in changed):
            continue  # already moved this cycle
        net = (e.get("transfers_in_event") or 0) - (e.get("transfers_out_event") or 0)
        if abs(net) >= MOMENTUM_THRESHOLD:
            momentum.append((eid, net))
    momentum.sort(key=lambda t: (-(t[0] in owned or t[0] in targets), -abs(t[1])))
    owned_target_momentum = [m for m in momentum if m[0] in owned or m[0] in targets]

    # --- decide whether to speak ----------------------------------------
    speak = bool(owned_changed or target_changed or owned_target_momentum or other_changed)
    lines = []
    if speak:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
        lines.append(f"PRICE WATCH · GW{event} · {stamp} UTC")

        if owned_changed:
            lines.append("\nYour squad:")
            for eid, delta in sorted(owned_changed, key=lambda t: t[1]):
                lines.append("  " + _fmt(eid, delta))
        if target_changed:
            lines.append("\nShortlist:")
            for eid, delta in sorted(target_changed, key=lambda t: -t[1]):
                lines.append("  " + _fmt(eid, delta))
        if other_changed:
            rises = sum(1 for _, d in other_changed if d > 0)
            falls = len(other_changed) - rises
            lines.append(f"\nElsewhere: {rises} risers, {falls} fallers.")
            notable = sorted(other_changed,
                             key=lambda t: -float(els[t[0]].get("selected_by_percent") or 0))[:3]
            for eid, delta in notable:
                lines.append("  " + _fmt(eid, delta))

        near = owned_target_momentum or momentum[:5]
        if near:
            lines.append("\nNear a change (approx, act before ~01:30 UTC):")
            for eid, net in near[:6]:
                e = els[eid]
                mark = _tag(eid)
                mark = f"{mark} " if mark else ""
                direction = "rising" if net > 0 else "falling"
                lines.append(f"  {mark}{e['web_name']} ({teams.get(e['team'], '?')}) "
                             f"{direction} · net {net:+,}")

    # --- always persist the new baseline -------------------------------
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump({"as_of": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "event": event, "prices": now_prices}, handle)

    return "\n".join(lines)


def main():
    try:
        report = build_report(FPLClient())
    except Exception as exc:  # noqa: BLE001 - a watch job must never crash its timer
        print(f"price watch error: {exc!r}", file=sys.stderr)
        return 0
    if report:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
