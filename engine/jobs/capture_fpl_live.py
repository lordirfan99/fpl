"""Capture raw FPL live event data (event/{gw}/live/) for BPS retraining.

Sol directive P7: raw responses are captured BEFORE parsing, with immutable
retention. BPS can change between provisional and finalized states, so we
capture after each fixture and again after finalization. This job runs
post-GW (cron) and stores:
  data/raw/live/event_{gw}_live_{ts}.json   (raw immutable payload)
  data/processed/live/gw{gw}_bps.csv        (derived per-player BPS/bonus)

Run: .venv/Scripts/python.exe jobs/capture_fpl_live.py --gw N
"""
import argparse
import csv
import json
import os
import sys
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "execution"))

from fpl_client import FPLClient

RAW_DIR = os.path.join(BASE, "data", "raw", "live")
PROC_DIR = os.path.join(BASE, "data", "processed", "live")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gw", type=int, required=True, help="gameweek to capture")
    args = ap.parse_args()
    gw = args.gw

    client = FPLClient()
    url = f"https://fantasy.premierleague.com/api/event/{gw}/live/"
    r = client.get_json(url)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(PROC_DIR, exist_ok=True)

    raw_path = os.path.join(RAW_DIR, f"event_{gw}_live_{ts}.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(r, f)
    print(f"raw saved -> {raw_path}")

    # derive per-player rows from explain (only finalized-ish snapshot)
    rows = []
    for fx in r.get("elements", []):
        for el in fx:
            stats = el.get("stats", {})
            rows.append({
                "element": el.get("id"),
                "fixture": fx.get("id") if isinstance(fx, dict) else None,
                "minutes": stats.get("minutes", 0),
                "bps": stats.get("bps", 0),
                "bonus": stats.get("bonus", 0),
                "goals_scored": stats.get("goals_scored", 0),
                "assists": stats.get("assists", 0),
                "clean_sheets": stats.get("clean_sheets", 0),
                "saves": stats.get("saves", 0),
                "recoveries": stats.get("recoveries", 0),
                "tackles": stats.get("tackles", 0),
                "clearances_blocks_interceptions": stats.get("clearances_blocks_interceptions", 0),
                "expected_goals": stats.get("expected_goals", 0),
                "expected_assists": stats.get("expected_assists", 0),
                "captured_at": ts,
            })
    csv_path = os.path.join(PROC_DIR, f"gw{gw}_bps.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["element"])
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"derived {len(rows)} player rows -> {csv_path}")


if __name__ == "__main__":
    main()
