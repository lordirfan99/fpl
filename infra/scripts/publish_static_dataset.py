"""Generate the free public dataset on a standard GitHub Actions runner.

No GCP identity, billing-enabled product, private account read or FPL write is
used. Publishing/atomic deployment is handled by GitHub Pages after success.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "infra" / "scripts"))
from refresh_live_leagues import collect  # noqa: E402
from app import live_fpl  # noqa: E402
from app.journal import build_gameweek_journal, verify_record_hash  # noqa: E402
from app.static_dataset import (LEAGUES, OWNER, SEASON, MAX_DATASET_BYTES, MAX_FILE_BYTES,
                                build_dataset, canonical, public_path, write_dataset)  # noqa: E402


def get_bytes(client: httpx.Client, url: str, limit: int = MAX_FILE_BYTES) -> bytes:
    parts, size = [], 0
    with client.stream("GET", url) as response:
        response.raise_for_status()
        for part in response.iter_bytes():
            size += len(part)
            if size > limit:
                raise ValueError("Public artifact exceeds size budget")
            parts.append(part)
    return b"".join(parts)


def restore_previous(client: httpx.Client, base: str, output: Path) -> tuple[dict[str, bytes], dict | None]:
    try:
        manifest = json.loads(get_bytes(client, base.rstrip("/") + "/manifest.json", 100_000))
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            return {}, None
        raise
    release, files = manifest.get("release"), manifest.get("files")
    if (manifest.get("schema_version") != 1 or not isinstance(release, str) or len(release) != 64
            or any(c not in "0123456789abcdef" for c in release) or not isinstance(files, dict) or len(files) > 200):
        raise ValueError("Invalid prior manifest")
    previous, size = {}, 0
    for name, digest in files.items():
        if not public_path(name) or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Unallowlisted prior file")
        raw = get_bytes(client, f"{base.rstrip('/')}/releases/{release}/{name}")
        size += len(raw)
        if size > MAX_DATASET_BYTES or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Prior dataset size or integrity check failed")
        previous[name] = raw
    # Retain one predecessor generation for clients holding a cached manifest.
    # Every retained file has just been allowlisted and checksum-verified.
    write_dataset(output, manifest, previous)
    return previous, manifest


def seed_history(client: httpx.Client, api: str, current_gw: int) -> dict[str, bytes]:
    """One-time import from the already-public legacy API; never renew timestamps."""
    previous = {}
    # If the public journal advertises frozen history, importing every record
    # is a cutover gate. A timeout must not silently erase that history or
    # replace a verified pre-deadline record with a newly inferred outcome.
    index = json.loads(get_bytes(client, f"{api}/v1/journal?season={SEASON}"))
    expected_journal = {row["gameweek"] for row in index.get("gameweeks", [])}
    if any(type(gw) is not int or not 1 <= gw <= 38 for gw in expected_journal):
        raise ValueError("Invalid legacy journal index")
    for league in LEAGUES:
        for gw in range(1, current_gw + 1):
            try:
                raw = get_bytes(client, f"{api}/v1/leagues/{league}/compact?gw={gw}")
                value = json.loads(raw)
                if (value.get("league_id") != league or value.get("gameweek") != gw
                        or value.get("encoding") != "league-squads-v1" or value.get("meta", {}).get("quality_status") != "valid"):
                    raise ValueError("Legacy archive identity mismatch")
                previous[f"leagues/{league}/gw/{gw}.json"] = raw
            except (httpx.HTTPError, ValueError):
                print(json.dumps({"message": "legacy_archive_unavailable", "league": league, "gameweek": gw}))
    for gw in sorted(expected_journal):
        try:
            raw = get_bytes(client, f"{api}/v1/journal/{SEASON}/gw/{gw}")
            entry = json.loads(raw)
            if entry.get("season") != SEASON or entry.get("gameweek") != gw or not verify_record_hash(entry):
                raise ValueError("Legacy journal integrity mismatch")
            previous[f"journal/{SEASON}/gw/{gw}.json"] = raw
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError(f"Required legacy journal GW{gw} could not be imported") from error
    return previous


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous-url", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--seed-api", help="One-time public archive import; ignored once a prior dataset exists")
    args = parser.parse_args()
    # Use a fresh isolated output tree. No working-tree data or private folders
    # are ever uploaded, and no dataset is committed to git.
    if args.output.exists():
        raise ValueError("Output must be a new directory")
    args.output.mkdir(parents=True)
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            previous, prior_manifest = restore_previous(client, args.previous_url, args.output)
            bootstrap = live_fpl._get("bootstrap-static/", ttl=0)
            fixtures = live_fpl._get("fixtures/", ttl=0)
            captured = datetime.now(timezone.utc).isoformat()
            gw = next(e["id"] for e in bootstrap["events"] if e.get("is_current"))
            if prior_manifest is None and args.seed_api:
                previous.update(seed_history(client, args.seed_api.rstrip("/"), gw))
            leagues = [collect(league) for league in LEAGUES]
            teams = {league: live_fpl.team(OWNER, gw, league) for league in LEAGUES}
            # New final outcomes may be recorded without pretending that a
            # pre-deadline prediction existed. The journal flags that gap.
            event = next(e for e in bootstrap["events"] if e["id"] == gw)
            journal_name = f"journal/{SEASON}/gw/{gw}.json"
            if event.get("finished") and event.get("data_checked") and journal_name not in previous:
                league = leagues[0]
                official_results = live_fpl._get(f"event/{gw}/live/", ttl=0)
                entry = build_gameweek_journal(season=SEASON, gameweek=gw, team_id=OWNER, league_id=league["league_id"],
                    snapshot={"competitors": league["managers"], "total_entries": league["expected_count"],
                              "fetched_at": league["captured_at"]},
                    analysis={"squad_ownership": {"avg_gw_points": sum(m["gw_points"] for m in league["managers"]) / league["expected_count"]}},
                    live=official_results)
                previous[journal_name] = canonical(entry)
            manifest, files = build_dataset(bootstrap=bootstrap, fixtures=fixtures, leagues=leagues, teams=teams,
                                           captured_at=captured, revision=args.revision, previous=previous)
            write_dataset(args.output, manifest, files)
            print(json.dumps({"message": "static_dataset_ready", "release": manifest["release"],
                              "files": len(files), "bytes": sum(map(len, files.values())),
                              "manager_counts": {str(p["league_id"]): p["expected_count"] for p in leagues}}))
    except BaseException:
        shutil.rmtree(args.output)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
