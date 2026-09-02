"""Finalize one public journal entry from completed, data-checked sources."""
from __future__ import annotations

import argparse, json, os, sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "services" / "api"))
from app.journal import build_gameweek_journal, build_index, journal_csv, read_journal_entries, write_immutable_record

def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

def config() -> dict:
    return read(ROOT / "data" / "journal" / "config.json")

def live(gw: int) -> dict:
    with urlopen(Request(f"https://fantasy.premierleague.com/api/event/{gw}/live/", headers={"User-Agent": "FPLScoutJournal/1.0"}), timeout=60) as response:
        return json.load(response)

def main() -> int:
    defaults = config(); parser = argparse.ArgumentParser(); parser.add_argument("--season"); parser.add_argument("--gw", type=int, required=True)
    parser.add_argument("--league", type=int); parser.add_argument("--team", type=int); parser.add_argument("--live-file")
    args = parser.parse_args(); args.season = args.season or defaults["active_season"]; args.league = args.league or int(defaults["primary_league_id"]); args.team = args.team or int(defaults["team_id"]); gw = args.gw
    snapshot = read(ROOT / "data" / f"gw{gw}_league{args.league}_data.json")
    analysis = read(ROOT / "reports" / f"GW{gw}" / f"GW{gw}_L{args.league}_analysis.json")
    if not snapshot or not analysis: raise SystemExit("Required completed-GW snapshot or analysis is missing")
    live_data = read(Path(args.live_file)) if args.live_file else live(gw)
    raw_path = ROOT / "data" / "journal-raw" / args.season / f"gw{gw:02d}" / "predeadline.json"
    bucket_name = os.getenv("FPL_JOURNAL_BUCKET") or os.getenv("FPL_SNAPSHOT_BUCKET")
    storage = None
    if bucket_name:
        try:
            from google.cloud import storage as gcs_storage
            storage = gcs_storage
        except ImportError:
            print("google-cloud-storage is unavailable; using local journal inputs", file=sys.stderr)
    if not raw_path.is_file() and bucket_name:
        try:
            if storage is None: raise RuntimeError("GCS client unavailable")
            blob = storage.Client().bucket(bucket_name).blob(f"journal-raw/{args.season}/gw{gw:02d}/predeadline.json")
            if blob.exists():
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text(blob.download_as_text(encoding="utf-8"), encoding="utf-8")
        except Exception as error:
            print(f"Journal archive unavailable: {error}", file=sys.stderr)
    predeadline = read(raw_path) or None
    public_path = ROOT / "data" / "journal-public-notes" / args.season / f"gw{gw:02d}.json"
    if not public_path.is_file() and bucket_name:
        try:
            if storage is None: raise RuntimeError("GCS client unavailable")
            blob = storage.Client().bucket(bucket_name).blob(f"journal-public/{args.season}/gw{gw:02d}/lesson.json")
            if blob.exists():
                public_path.parent.mkdir(parents=True, exist_ok=True)
                public_path.write_text(blob.download_as_text(encoding="utf-8"), encoding="utf-8")
        except Exception as error:
            print(f"Public journal lesson unavailable: {error}", file=sys.stderr)
    public_note = read(public_path).get("public_lesson")
    entry = build_gameweek_journal(season=args.season, gameweek=gw, team_id=args.team, league_id=args.league,
                                   snapshot=snapshot, analysis=analysis, live=live_data, predeadline=predeadline, public_lesson=public_note)
    target_dir = ROOT / "data" / "journal" / args.season; target_dir.mkdir(parents=True, exist_ok=True)
    try:
        write_immutable_record(target_dir / f"gw{gw:02d}.json", entry)
    except (ValueError, FileExistsError) as error:
        raise SystemExit(str(error)) from error
    entries = read_journal_entries(ROOT / "data", args.season)
    (target_dir / "index.json").write_text(json.dumps(build_index(entries, args.season), indent=2) + "\n", encoding="utf-8")
    exports = target_dir / "exports"; exports.mkdir(exist_ok=True)
    (exports / "gameweeks.csv").write_text(journal_csv(entries), encoding="utf-8")
    player_lines = ["season,gameweek,element,name,team,position,role,points,minutes"]
    for row in entries:
        for player in row.get("outcome", {}).get("squad", []):
            role = "captain" if player.get("is_captain") else "vice" if player.get("is_vice_captain") else "starter" if player.get("multiplier") else "bench"
            values = [row["season"], row["gameweek"], player.get("element"), player.get("name"), player.get("team"), player.get("position"), role, player.get("points"), player.get("minutes")]
            player_lines.append(",".join('"' + str(value).replace('"', '""') + '"' for value in values))
    (exports / "players.csv").write_text("\n".join(player_lines) + "\n", encoding="utf-8")
    (exports / "manifest.json").write_text(json.dumps({"schema_version": 1, "season": args.season,
        "gameweeks": [row["gameweek"] for row in entries], "private_notes_included": False}, indent=2) + "\n", encoding="utf-8")
    (exports / "README.md").write_text(f"# FPL Journal {args.season}\n\nGenerated research exports. Private Telegram reflections are excluded. Full immutable prediction evidence is retained in GCS.\n", encoding="utf-8")
    print(f"Finalized {args.season} GW{gw} journal ({entry['quality']['status']})"); return 0

if __name__ == "__main__": sys.exit(main())
