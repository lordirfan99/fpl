#!/usr/bin/env python3
"""FPL Autopilot - post-gameweek manager digest.

A short "how did I do" card sent once per finalized + data-checked gameweek:
GW points vs the FPL average, bench waste, overall-rank movement, and rank
moves in each tracked prize league. Distinct from post_gw_review.py, which is
the model's accuracy report card.

Self-gating: prints nothing (exit 0) unless a newly finalized GW is waiting.
Read-only.
"""
import json
import os
import sys

import project_paths

BASE = str(project_paths.resolve_project_root(__file__))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "."))

TEAM_ID = int(os.environ.get("FPL_TEAM_ID", "2797967"))
STATE_FILE = os.path.join(BASE, "data", "processed", "post_gw_digest_state.json")
LEAGUE_STATE = os.path.join(BASE, "data", "processed", "league_intelligence", "latest.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _settings():
    return _load(os.path.join(BASE, "config", "settings.json")) or {}


def _arrow(delta):
    if delta > 0:
        return f"🟢 +{delta:,}"
    if delta < 0:
        return f"🔴 {delta:,}"
    return "▪️ 0"


def build_digest(client):
    bootstrap = client.get_json("bootstrap-static/")
    events = {e["id"]: e for e in bootstrap.get("events", [])}
    finalized = [e["id"] for e in bootstrap.get("events", [])
                 if e.get("finished") and e.get("data_checked")]
    if not finalized:
        return None, None
    gw = max(finalized)
    if (_load(STATE_FILE) or {}).get("last_digest_gw", 0) >= gw:
        return None, None

    history = client.get_json(f"entry/{TEAM_ID}/history/")
    rows = {int(r["event"]): r for r in history.get("current", [])}
    this_row = rows.get(gw)
    if not this_row:
        return None, None
    prev = _load(STATE_FILE) or {}

    avg = events.get(gw, {}).get("average_entry_score")
    pts = int(this_row.get("points") or 0)
    bench = int(this_row.get("points_on_bench") or 0)
    hit = int(this_row.get("event_transfers_cost") or 0)
    overall = int(this_row.get("overall_rank") or 0)
    total = int(this_row.get("total_points") or 0)

    lines = [f"📊 <b>GW{gw} DIGEST</b>"]
    vs_avg = f" (avg {avg}, {pts - avg:+d})" if isinstance(avg, int) else ""
    tail = f" · −{hit} hit" if hit else ""
    lines.append(f"You: <b>{pts}</b> pts{vs_avg} · bench {bench}{tail}")
    lines.append(f"Season total: {total:,}")
    if overall:
        prev_overall = int(prev.get("overall_rank") or 0)
        if prev_overall:
            lines.append(f"Overall: {prev_overall:,} → {overall:,}  {_arrow(prev_overall - overall)}")
        else:
            lines.append(f"Overall rank: {overall:,}")

    # --- league moves from the finalized intelligence snapshot ---
    league_state = _load(LEAGUE_STATE) or {}
    standings = league_state.get("standings") or []
    our_entry = league_state.get("our_entry") or TEAM_ID
    league_ids = ((_settings().get("league_intelligence") or {}).get("league_ids")
                  or league_state.get("league_ids") or [])
    prev_leagues = prev.get("leagues") or {}
    now_leagues = {}
    league_names = {row.get("league_id"): row.get("league_name") for row in
                   (league_state.get("leagues") or []) if row.get("league_id")}
    for lid in league_ids:
        row = next((r for r in standings
                    if r.get("entry") == our_entry and r.get("league_id") == lid), None)
        if not row or row.get("rank") is None:
            continue
        rank = int(row["rank"])
        now_leagues[str(lid)] = rank
        name = league_names.get(lid) or row.get("entry_name") or f"L{lid}"
        prev_rank = prev_leagues.get(str(lid))
        if prev_rank:
            moved = int(prev_rank) - rank
            passed = f" (passed {moved})" if moved > 0 else (f" (dropped {-moved})" if moved < 0 else "")
            lines.append(f"L{lid} {name}: {int(prev_rank):,} → {rank:,}  "
                         f"{'🟢' if moved > 0 else '🔴' if moved < 0 else '▪️'} {moved:+d}{passed}")
        else:
            lines.append(f"L{lid} {name}: rank {rank:,}")

    new_state = {"last_digest_gw": gw, "overall_rank": overall, "leagues": now_leagues}
    return "\n".join(lines), new_state


def main():
    from fpl_client import FPLClient

    try:
        text, state = build_digest(FPLClient())
    except Exception as exc:  # noqa: BLE001 - a digest must never crash the runner
        print(f"post_gw_digest error: {exc!r}", file=sys.stderr)
        return 0
    if not text:
        return 0
    print(text)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    return 1


if __name__ == "__main__":
    sys.exit(main())
