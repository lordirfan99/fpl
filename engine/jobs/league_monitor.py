"""League competitor monitor — match-week surveillance for the RM3000 league.

Fetches, for a target league:
  - Standings (paginated) — rank, points, movement
  - Each tracked opponent's current-GW picks + captain
  - LIVE points during the match week (event/{gw}/live/)
  - Per-opponent live GW total, captain points, rank delta

Output: data/processed/league_monitor_gw{N}.json (snapshot) + optional alert
string when rank movements / threat signals cross thresholds.

API reality (verified 2026-08-12, pre-GW1):
  - standings are EMPTY until GW1 completes; entries appear via standings after
    the first GW, so the monitor "comes alive" at GW1 deadline
  - entry/{id}/event/{gw}/picks/ 404s until the GW goes live
  - event/{gw}/live/ is empty pre-match
The job must therefore handle empty inputs gracefully and stay silent.

Run: .venv/Scripts/python.exe jobs/league_monitor.py --league 687126 --gw 1 [--track 20]
"""
import argparse
import json
import os
import sys
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "execution"))

from fpl_client import FPLClient

OUT_DIR = os.path.join(BASE, "data", "processed")

# Alert thresholds (tunable)
RANK_MOVE_ALERT = 5        # green/red arrow bigger than this -> alert
CAPTAIN_PTS_ALERT = 12     # opponent captain live pts above this -> alert
THREAT_GAP_PTS = 20        # opponent within this many pts of us -> threat


def fetch_standings(client, league_id, max_pages=10):
    """Paginated standings. Returns {entry_id: {entry_name, player_name, rank,
    last_rank, total, entry}}."""
    out = {}
    page = 1
    while page <= max_pages:
        try:
            s = client.get_json(f"leagues-classic/{league_id}/standings/?page_standings={page}")
        except Exception:
            break
        st = s.get("standings", {})
        results = st.get("results", []) or []
        if not results:
            break
        for r in results:
            out[r["entry"]] = {
                "entry": r["entry"], "entry_name": r.get("entry_name"),
                "player_name": r.get("player_name"), "rank": r.get("rank"),
                "last_rank": r.get("last_rank"), "total": r.get("total"),
            }
        if not st.get("has_next"):
            break
        page += 1
    return out


def fetch_picks_live(client, entry_id, gw, elements_live, el_names):
    """Return {picks: [...], captain_id, captain_name, gw_live_pts} for one entry.

    TRUST BOUNDARY (Sol directive §6): current-GW picks are NOT trustworthy
    before deadline_time + 5 minutes, regardless of HTTP status. We only fetch
    picks when the GW is live; before that we return None so no competitor
    content enters user-visible or strategy paths.
    """
    try:
        p = client.entry_picks(entry_id, gw)
    except Exception:
        return None
    picks = p.get("picks", []) or []
    if len(picks) < 15:
        return None  # incomplete/untrusted payload
    captain_id = None
    for pk in picks:
        if pk.get("is_captain"):
            captain_id = pk["element"]
            break
    gw_live = 0.0
    for pk in picks:
        el_id = pk["element"]
        mult = float(pk.get("multiplier", 1) or 1)
        live_pts = 0.0
        if el_id in elements_live:
            live_pts = float(elements_live[el_id].get("points", 0) or 0)
        gw_live += live_pts * mult
    cap_pts = 0.0
    if captain_id is not None and captain_id in elements_live:
        cap_pts = float(elements_live[captain_id].get("points", 0) or 0) * 2
    return {
        "picks": picks,
        "captain_id": captain_id,
        "captain_name": el_names.get(captain_id, "?"),
        "gw_live_pts": round(gw_live, 1),
        "captain_live_pts": round(cap_pts, 1),
    }


def gw_is_live(client, gw):
    """True only after the GW deadline has passed (bootstrap deadline_time,
    UTC, +5 min trust buffer). Sol §6: never trust picks pre-deadline."""
    try:
        import json
        bootstrap = json.load(open(os.path.join(BASE, "data", "raw", "bootstrap-static.json"), encoding="utf-8"))
        for ev in bootstrap["events"]:
            if int(ev["id"]) == gw:
                dl = ev.get("deadline_time", "")
                if not dl:
                    return False
                from datetime import datetime, timezone, timedelta
                dl_dt = datetime.fromisoformat(dl.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                return now >= dl_dt + timedelta(minutes=5)
    except Exception:
        pass
    return False


def fetch_live_elements(client, gw):
    """event/{gw}/live/ -> {element_id: {points, minutes, bps, bonus}}."""
    try:
        live = client.get_json(f"event/{gw}/live/")
    except Exception:
        return {}
    out = {}
    for fx in live.get("elements", []) or []:
        for el in fx:
            stats = el.get("stats", {}) or {}
            out[el["id"]] = {
                "points": stats.get("points", 0),
                "minutes": stats.get("minutes", 0),
                "bps": stats.get("bps", 0),
                "bonus": stats.get("bonus", 0),
            }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", type=int, required=True)
    ap.add_argument("--gw", type=int, required=True)
    ap.add_argument("--track", type=int, default=20, help="top-N opponents to track")
    ap.add_argument("--our-entry", type=int, default=2797967)
    args = ap.parse_args()

    client = FPLClient()
    bootstrap = json.load(open(os.path.join(BASE, "data", "raw", "bootstrap-static.json"), encoding="utf-8"))
    el_names = {e["id"]: e["web_name"] for e in bootstrap["elements"]}

    standings = fetch_standings(client, args.league)
    if not standings:
        print("STANDINGS EMPTY (pre-season or no GW data yet) - silent exit")
        return

    live = fetch_live_elements(client, args.gw) if gw_is_live(client, args.gw) else {}
    picks_trusted = bool(live)  # only post-deadline do we treat picks as real
    ranked = sorted(standings.values(), key=lambda r: (r.get("rank") or 10**9))
    track = [r for r in ranked if r["entry"] != args.our_entry][:args.track]

    snapshot = {
        "league": args.league, "gw": args.gw, "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "our_entry": args.our_entry,
        "standings_count": len(standings),
        "entries": {},
    }
    our = standings.get(args.our_entry, {})
    our_pts = float(our.get("total", 0) or 0)

    alerts = []
    for r in track:
        live_info = fetch_picks_live(client, r["entry"], args.gw, live, el_names) if picks_trusted else None
        entry = {
            "entry": r["entry"], "entry_name": r.get("entry_name"),
            "player_name": r.get("player_name"), "rank": r.get("rank"),
            "last_rank": r.get("last_rank"),
            "rank_delta": (r.get("last_rank") or 0) - (r.get("rank") or 0) if r.get("rank") and r.get("last_rank") else None,
            "total": r.get("total"),
            "picks_trusted": picks_trusted,
        }
        if live_info:
            entry.update(live_info)
            gap = entry["total"] - our_pts if entry.get("total") is not None else None
            entry["gap_to_us"] = round(gap, 1) if gap is not None else None
            if entry["rank_delta"] is not None and abs(entry["rank_delta"]) >= RANK_MOVE_ALERT:
                alerts.append(f"🔴 {entry['entry_name']} moved {entry['rank_delta']:+d} to rank {entry['rank']}")
            if live_info["captain_live_pts"] >= CAPTAIN_PTS_ALERT:
                alerts.append(f"👑 {entry['entry_name']}'s captain {live_info['captain_name']} on {live_info['captain_live_pts']} pts (live)")
            if entry.get("gap_to_us") is not None and -THREAT_GAP_PTS <= entry["gap_to_us"] <= THREAT_GAP_PTS:
                alerts.append(f"⚠️ THREAT: {entry['entry_name']} within {entry['gap_to_us']:+.0f} pts of us")
        snapshot["entries"][str(r["entry"])] = entry

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"league_monitor_gw{args.gw}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=1)
    print(f"snapshot saved -> {out_path} | tracked {len(track)} opponents")
    if alerts:
        print("\n".join(alerts))


if __name__ == "__main__":
    main()
