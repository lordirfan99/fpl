"""Collect and atomically publish complete live league snapshots.

This is a background-only collector. It must never be called from a web request:
the public API reads the last validated manifest instead. A snapshot is published
only when every manager in the official standings has a valid current squad.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from app import live_fpl  # noqa: E402

try:
    from google.cloud import storage
except ImportError:  # pragma: no cover - checked at runtime in production
    storage = None


# v2: each manager also carries `gw_bank` and a real `overall_rank` /
# `transfers_made` lifted from the picks payload's `entry_history` block, so the
# published snapshot is a complete recommendation input. v1 readers ignore the
# extra keys.
SCHEMA_VERSION = 2


def _manager(row: dict[str, Any]) -> dict[str, Any]:
    squad = row.get("_live_squad", [])
    league_rank = int(row.get("rank") or 0)
    # The classic-standings feed has no overall rank; `entry_history.overall_rank`
    # from the per-manager picks call does. Prefer it; fall back to league rank.
    official_overall = row.get("_live_overall_rank")
    overall_rank = int(official_overall) if official_overall else league_rank
    return {
        "entry_id": int(row.get("entry") or 0),
        "entry_name": row.get("entry_name") or "",
        "player_name": row.get("player_name") or "",
        "gw_points": int(row.get("event_total") or 0),
        "total_points": int(row.get("total") or 0),
        "overall_rank": overall_rank,
        "overall_rank_source": "official-entry-history" if official_overall else "classic-league-rank-fallback",
        "league_rank": league_rank,
        "league_last_rank": int(row.get("last_rank") or 0),
        "squad_cost": round(sum(float(pick.get("cost") or 0) for pick in squad), 1),
        # `bank` is FPL-native tenths (100 == £10.0m). Absent only if the picks
        # payload had no `entry_history`.
        "gw_bank": None if row.get("_live_bank") is None else int(row["_live_bank"]),
        "captain": row.get("_live_captain") or "",
        "transfers_made": int(row.get("_live_event_transfers") or 0),
        "squad": squad,
    }


def _validate(managers: list[dict[str, Any]], expected_count: int) -> None:
    if len(managers) != expected_count or not managers:
        raise RuntimeError(f"manager count mismatch: expected {expected_count}, got {len(managers)}")
    ids = {manager["entry_id"] for manager in managers}
    if 0 in ids or len(ids) != expected_count:
        raise RuntimeError("duplicate or invalid FPL entry ids")
    for manager in managers:
        squad = manager["squad"]
        if len(squad) != 15:
            raise RuntimeError(f"entry {manager['entry_id']} has {len(squad)} picks")
        starters = sum(1 for pick in squad if int(pick.get("multiplier") or 0) > 0)
        captains = sum(1 for pick in squad if pick.get("is_captain"))
        vices = sum(1 for pick in squad if pick.get("is_vice_captain"))
        # Bench Boost has 15 scoring picks. A legitimate chip must not prevent
        # publication of the entire league.
        if starters not in (11, 15) or captains != 1 or vices != 1:
            raise RuntimeError(
                f"entry {manager['entry_id']} invalid lineup: starters={starters}, captains={captains}, vices={vices}"
            )


def collect(league_id: int) -> dict[str, Any]:
    gameweek = live_fpl.current_gameweek()
    standings = live_fpl.league_standings(league_id)
    rows = standings["managers"]
    expected_count = int(standings["count"])
    hydrated = live_fpl.hydrate_manager_squads(rows, gameweek, expected_count)
    if hydrated != expected_count:
        raise RuntimeError(f"GW{gameweek} league {league_id}: hydrated {hydrated}/{expected_count} squads")
    managers = [_manager(row) for row in rows]
    _validate(managers, expected_count)
    captured_at = datetime.now(timezone.utc).isoformat()
    ranks_official = all(m["overall_rank_source"] == "official-entry-history" for m in managers)
    banks_present = all(m["gw_bank"] is not None for m in managers)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "complete",
        "source": "official-fpl-live",
        "captured_at": captured_at,
        "league_id": league_id,
        "gameweek": gameweek,
        "expected_count": expected_count,
        "hydrated_count": hydrated,
        "pages_fetched": int(standings.get("pages_fetched") or 0),
        "rank_provenance": "official-entry-history" if ranks_official else "classic-league-rank-fallback",
        "bank_provenance": "official-entry-history" if banks_present else "unavailable",
        "managers": managers,
    }


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def publish(bucket_name: str, payload: dict[str, Any]) -> tuple[str, str]:
    if storage is None:
        raise RuntimeError("google-cloud-storage is required to publish live snapshots")
    bucket = storage.Client().bucket(bucket_name)
    captured = payload["captured_at"].replace(":", "-").replace("+00:00", "Z")
    object_name = f"live/gw{payload['gameweek']}/league{payload['league_id']}/runs/{captured}-{uuid.uuid4().hex}.json"
    blob = bucket.blob(object_name)
    encoded = _canonical(payload)
    digest = hashlib.sha256(encoded).hexdigest()
    blob.upload_from_string(encoded, content_type="application/json", if_generation_match=0)

    manifest_name = f"live/league{payload['league_id']}/current.json"
    manifest_blob = bucket.blob(manifest_name)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "status": "complete",
        "snapshot_object": object_name,
        "snapshot_sha256": digest,
        "captured_at": payload["captured_at"],
        "league_id": payload["league_id"],
        "gameweek": payload["gameweek"],
        "expected_count": payload["expected_count"],
        "hydrated_count": payload["hydrated_count"],
    }
    manifest_data = _canonical(manifest)
    for attempt in range(3):
        try:
            if manifest_blob.exists():
                manifest_blob.reload()
                manifest_blob.upload_from_string(
                    manifest_data, content_type="application/json", if_generation_match=manifest_blob.generation
                )
            else:
                manifest_blob.upload_from_string(manifest_data, content_type="application/json", if_generation_match=0)
            return object_name, digest
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", default=os.getenv("FPL_SNAPSHOT_BUCKET") or os.getenv("FPL_JOURNAL_BUCKET"))
    parser.add_argument("--league", type=int, nargs="+", default=[58005, 131997])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.dry_run and not args.bucket:
        parser.error("--bucket or FPL_SNAPSHOT_BUCKET is required")
    for league_id in args.league:
        payload = collect(league_id)
        if args.dry_run:
            print(json.dumps({key: value for key, value in payload.items() if key != "managers"}, sort_keys=True))
        else:
            object_name, digest = publish(args.bucket, payload)
            print(json.dumps({"league_id": league_id, "object": object_name, "sha256": digest, "managers": payload["hydrated_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
