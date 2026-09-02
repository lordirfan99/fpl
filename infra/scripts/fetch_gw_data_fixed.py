#!/usr/bin/env python3
"""Corrected and faster multi-league GW fetcher.

Fixes:
1. Managers appearing in multiple tracked leagues are retained in every league.
2. Transfers use /entry/{id}/transfers/ and are filtered by gameweek.
3. Unique manager snapshots are fetched concurrently with bounded workers.
"""

import argparse
import copy
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import fetch_gw_data as base

DATA_DIR = base.DATA_DIR


def get_entry_transfers_fixed(entry_id, gw):
    data = base.api_get(f"entry/{entry_id}/transfers/")
    if not data:
        return {"transfers": []}
    if isinstance(data, list):
        return {"transfers": [t for t in data if t.get("event") == gw]}
    transfers = data.get("transfers", []) if isinstance(data, dict) else []
    return {"transfers": [t for t in transfers if t.get("event") == gw]}


base.get_entry_transfers = get_entry_transfers_fixed


def write_outputs(gw, league_id, competitors, errors, *, allow_correction=False):
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    full_path = os.path.join(DATA_DIR, f"gw{gw}_league{league_id}_data.json")
    compact_path = os.path.join(DATA_DIR, f"gw{gw}_league{league_id}_compact.json")
    existing = [path for path in (full_path, compact_path) if os.path.exists(path)]
    if existing and not allow_correction:
        raise FileExistsError(
            "refusing to overwrite finalized snapshot(s): " + ", ".join(existing)
            + "; use --allow-correction only with a documented correction"
        )
    payload = {
        "gw": gw,
        "league_id": league_id,
        "fetched_at": fetched_at,
        "total_entries": len(competitors),
        "errors": errors,
        "competitors": competitors,
    }
    with open(full_path, "w") as f:
        json.dump(payload, f, indent=2)

    compact = {
        "gw": gw,
        "league_id": league_id,
        "fetched_at": fetched_at,
        "total_entries": len(competitors),
        "competitors": [
            {
                "entry_id": c["entry_id"],
                "entry_name": c.get("entry_name", ""),
                "player_name": c.get("player_name", ""),
                "league_rank": c.get("league_rank", 0),
                "gw_points": c.get("gw_points", 0),
                "total_points": c.get("total_points", 0),
                "rank": c.get("rank", 0),
                "squad_cost": c.get("squad_cost", 0),
                "captain": c.get("captain", "N/A"),
                "vice_captain": c.get("vice_captain", "N/A"),
                "transfers_made": c.get("transfers_made", 0),
                "injured_count": c.get("injured_count", 0),
                "active_players_count": c.get("active_players_count", 0),
                "squad_composition": c.get("squad_composition", {}),
                "squad_teams": c.get("squad_teams", {}),
            }
            for c in competitors
        ],
    }
    with open(compact_path, "w") as f:
        json.dump(compact, f, indent=2)
    print(f"League {league_id}: {len(competitors)} competitors -> {full_path}", file=sys.stderr)


def fetch_one(entry_id, gw, player_map):
    try:
        return entry_id, base.fetch_competitor_data(entry_id, gw, player_map), None
    except Exception as exc:
        return entry_id, {"entry_id": entry_id, "gw": gw, "fetch_error": str(exc)}, str(exc)


def main():
    parser = argparse.ArgumentParser(description="Corrected FPL multi-league GW fetcher")
    parser.add_argument("--gw", type=int, required=True)
    parser.add_argument("--league", type=int, nargs="+", default=[58005, 131997])
    parser.add_argument("--max", type=int, default=3000)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--allow-correction", action="store_true",
                        help="Explicitly replace an existing finalized snapshot")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    bootstrap = base.load_bootstrap()
    if not bootstrap:
        print("Failed to load FPL bootstrap-static", file=sys.stderr)
        return 1
    player_map = base.build_player_map(bootstrap)

    standings_by_league = {}
    all_entry_ids = set()
    for league_id in args.league:
        standings = base.get_league_standings(league_id, args.gw)
        if not standings:
            fallback = base.load_entry_ids_from_scout_data([league_id]).get(league_id, [])
            standings = [
                {
                    "entry_id": e["entry_id"],
                    "entry_name": e.get("entry_name", ""),
                    "player_name": e.get("player_name", ""),
                    "rank": i + 1,
                    "total_points": 0,
                    "last_rank": 0,
                }
                for i, e in enumerate(fallback)
            ]
        standings = standings[: args.max]
        standings_by_league[league_id] = standings
        all_entry_ids.update(s["entry_id"] for s in standings if s.get("entry_id"))
        print(f"League {league_id}: {len(standings)} standings entries", file=sys.stderr)

    ids = sorted(all_entry_ids)
    cache = {}
    errors = 0
    workers = max(1, min(args.workers, 24))
    print(f"Fetching {len(ids)} unique managers with {workers} workers", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_one, entry_id, args.gw, player_map) for entry_id in ids]
        for i, future in enumerate(as_completed(futures), 1):
            entry_id, data, error = future.result()
            cache[entry_id] = data
            if error:
                errors += 1
                print(f"Failed entry {entry_id}: {error}", file=sys.stderr)
            if i % 100 == 0 or i == len(ids):
                print(f"Fetched {i}/{len(ids)} unique managers (errors={errors})", file=sys.stderr)

    for league_id, standings in standings_by_league.items():
        competitors = []
        for standing in standings:
            entry_id = standing.get("entry_id")
            if not entry_id:
                continue
            c = copy.deepcopy(cache[entry_id])
            c["league_id"] = league_id
            c["league_rank"] = standing.get("rank", 0)
            c["entry_name"] = standing.get("entry_name", "")
            c["player_name"] = standing.get("player_name", "")
            c["league_total"] = standing.get("total_points", 0)
            c["league_last_rank"] = standing.get("last_rank", 0)
            competitors.append(c)
        write_outputs(args.gw, league_id, competitors, errors, allow_correction=args.allow_correction)

    print(f"Done: {len(ids)} unique managers across {len(args.league)} leagues; errors={errors}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
