"""Build an allowlisted public dataset. No private packets or FPL writes.

Publication is a content-addressed directory plus a small manifest. Consumers
verify each body against the manifest, so a cache/deployment transition cannot
silently combine snapshots from different runs.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .compact_league import compact_managers
from .decision_context import current_context
from .journal import build_index, journal_csv, verify_record_hash
from .projections import build_projections
from .projection_types import PROJECTION_VERSION
from .reference_snapshots import reference_payloads
from .validation import snapshot_quality
from .recommendations import build_recommendations, MODEL_VERSION

LEAGUES = (58005, 131997)
OWNER = 2797967
SEASON = "2026-27"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_DATASET_BYTES = 250 * 1024 * 1024


def public_path(path: str) -> bool:
    return bool(re.fullmatch(
        r"(?:catalog|fixtures|projections)\.json|"
        r"leagues/(?:58005|131997)/(?:live|decision|team|research)\.json|"
        r"leagues/(?:58005|131997)/gw/(?:[1-9]|[12][0-9]|3[0-8])\.json|"
        r"journal/2026-27/(?:index\.json|gameweeks\.csv|gw/(?:[1-9]|[12][0-9]|3[0-8])\.json)", path))


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def compact_snapshot(payload: dict, *, finalized: bool = False) -> dict:
    from .live_freshness import snapshot_freshness
    from .schemas import Manager
    managers = payload["managers"]
    league_id, gw = payload["league_id"], payload["gameweek"]
    if league_id not in LEAGUES or not isinstance(gw, int) or not 1 <= gw <= 38:
        raise ValueError("Unexpected league or gameweek")
    if (payload.get("status") != "complete" or not managers
            or len(managers) != payload.get("expected_count")
            or len(managers) != payload.get("hydrated_count")):
        raise ValueError("Partial league cannot be published")
    # Model/schema and chip-aware shape validation, not just list length.
    for row in managers:
        Manager.model_validate(row)
    status, _ = snapshot_quality({"competitors": managers, "total_entries": len(managers)})
    if status != "valid":
        raise ValueError("Invalid recorded squads")
    captured = payload["captured_at"]
    freshness = snapshot_freshness(captured)
    if freshness["stale"]:
        raise ValueError("New collection is already stale or has an invalid capture time")
    return {"meta": {"source": "official-fpl-scheduled-snapshot", "snapshot_at": captured,
                     "generated_at": captured, "snapshot_gameweek": gw, "quality_status": "valid", **freshness},
            "league_id": league_id, "gameweek": gw, "count": len(managers), "declared_count": len(managers),
            "hydration_percent": 100.0, "provisional": not finalized, **compact_managers(managers)}


def validate_archive(raw: bytes, league_id: int, gameweek: int) -> None:
    from .schemas import Manager, Pick
    value = json.loads(raw)
    rows, pool = value.get("managers"), value.get("players")
    if (value.get("encoding") != "league-squads-v1" or value.get("league_id") != league_id
            or value.get("gameweek") != gameweek or value.get("provisional") is True
            or not isinstance(rows, list) or not rows or not isinstance(pool, list)
            or value.get("count") != len(rows) or value.get("declared_count") != len(rows)):
        raise ValueError("Invalid frozen archive")
    seen = set()
    for row in rows:
        refs = row.get("squad")
        if (type(row.get("entry_id")) is not int or row["entry_id"] <= 0 or row["entry_id"] in seen
                or not isinstance(refs, list) or len(refs) != 15):
            raise ValueError("Invalid archive population")
        seen.add(row.get("entry_id"))
        picks = []
        for ref in refs:
            if (not isinstance(ref, list) or len(ref) != 4 or type(ref[0]) is not int
                    or not 0 <= ref[0] < len(pool) or type(ref[1]) is not int
                    or type(ref[2]) is not bool or type(ref[3]) is not bool):
                raise ValueError("Invalid archive reference")
            picks.append(Pick.model_validate({**pool[ref[0]], "multiplier": ref[1],
                                              "is_captain": ref[2], "is_vice_captain": ref[3]}).model_dump())
        Manager.model_validate({**row, "squad": picks})


def build_dataset(*, bootstrap: dict, fixtures: list, leagues: list[dict], teams: dict[int, dict],
                  captured_at: str, revision: str, previous: dict[str, bytes] | None = None) -> tuple[dict, dict[str, bytes]]:
    """Only this function chooses public files; arbitrary input files are never uploaded."""
    catalog, horizon = reference_payloads(bootstrap, fixtures, captured_at)
    if {p["league_id"] for p in leagues} != set(LEAGUES) or len(leagues) != len(LEAGUES):
        raise ValueError("Both complete leagues are required")
    if set(teams) != set(LEAGUES):
        raise ValueError("Both public owner-team contexts are required")
    files: dict[str, bytes] = {}
    # Preserve frozen public history, never current data or arbitrary files.
    for name, raw in (previous or {}).items():
        if not public_path(name) or len(raw) > MAX_FILE_BYTES:
            raise ValueError("Unallowlisted or oversized prior artifact")
        if "/gw/" in name:
            if name.startswith("journal/"):
                entry = json.loads(raw)
                if not verify_record_hash(entry):
                    raise ValueError("Journal record hash failed")
            else:
                _, league, _, filename = name.split("/")
                validate_archive(raw, int(league), int(filename.removesuffix(".json")))
            files[name] = raw

    def put(name, value):
        if not public_path(name):
            raise ValueError("Artifact path is not public")
        files[name] = canonical(value)

    meta = {"source": "official-fpl-scheduled-snapshot", "snapshot_at": captured_at,
            "generated_at": captured_at, "quality_status": "valid", "code_revision": revision}
    player_keys = ("id", "photo", "team", "event_points", "web_name", "element_type", "now_cost",
                   "ep_next", "form", "selected_by_percent", "status", "news")
    event_keys = ("id", "name", "is_current", "is_next", "finished", "data_checked", "deadline_time")
    put("catalog.json", {"meta": meta, "players": [{k: p.get(k) for k in player_keys} for p in catalog["elements"]],
                         "teams": [{k: t.get(k) for k in ("id", "name", "short_name", "code")} for t in catalog["teams"]],
                         "events": [{k: e.get(k) for k in event_keys} for e in catalog["events"]]})
    put("fixtures.json", horizon)
    current = next((e["id"] for e in catalog["events"] if e.get("is_current")), None)
    if current is None:
        raise ValueError("No official current gameweek")
    target = min(current + 1, 38)
    projections = build_projections(catalog, horizon["gameweeks"][str(target)])
    put("projections.json", {"meta": meta, "gameweek": target, "projection_version": PROJECTION_VERSION,
                             "players": [p.to_dict() for p in projections]})
    available = {}
    for payload in leagues:
        league_id, gw = payload["league_id"], payload["gameweek"]
        if gw != current:
            raise ValueError("League collection crossed a gameweek transition")
        compact = compact_snapshot(payload)
        put(f"leagues/{league_id}/live.json", compact)
        context = current_context(payload, league_id, OWNER)
        put(f"leagues/{league_id}/decision.json", context)
        owner = next((m for m in payload["managers"] if m["entry_id"] == OWNER), None)
        if owner is not None:
            research = build_recommendations(owner, payload["managers"], catalog, horizon["gameweeks"][str(target)],
                                             population_size=payload["expected_count"], gameweek=gw)
            research["transfers"] = []
            research["captains"] = []
            research["inputs"].update({"account_state_verified": False, "scope": "league_research"})
            put(f"leagues/{league_id}/research.json", {**research, "meta": {**meta, "snapshot_at": payload["captured_at"]},
                "gameweek": gw, "model_version": MODEL_VERSION, "packet_status": "advisory", "plan": None,
                "freshness": {"source": "official-fpl-scheduled-snapshot", "status": "provisional", "stale": False,
                    "snapshot_at": payload["captured_at"], "account_state_verified": False, "account_gameweek": gw,
                    "reason": "Scheduled public league research; current account state is not verified",
                    "scope": "league_research", "missing_fields": []},
                "execution_authority": "manual_fpl", "writes_enabled": False, "executable": False})

        team = teams[league_id]
        if team.get("gameweek") != gw or team.get("entry", {}).get("id") != OWNER or len(team.get("picks", [])) != 15:
            raise ValueError("Owner team identity mismatch")
        # The entire team object is assembled by the public official-FPL reader,
        # not the authenticated account exporter. No private directory is read.
        public_team = {k: team.get(k) for k in ("gameweek", "fetched_at", "provisional", "active_chip", "chips_played",
                                              "points", "points_source", "fixtures")}
        public_team["entry"] = {k: team["entry"].get(k) for k in ("id", "entry_name", "player_name", "overall_rank",
                                                               "total_points", "value", "bank", "transfers_made", "transfers_cost")}
        public_team["league"] = ({k: team["league"].get(k) for k in ("id", "name", "entry_rank", "entry_last_rank", "rank_count")}
                                  if isinstance(team.get("league"), dict) else None)
        public_team["picks"] = [{k: p.get(k) for k in ("element", "position", "multiplier", "is_captain", "is_vice_captain",
                                                     "web_name", "team", "points", "now_cost")} for p in team["picks"]]
        put(f"leagues/{league_id}/team.json", {**public_team, "source": "official-fpl-scheduled-snapshot", "status": "recorded"})
        event = next(e for e in catalog["events"] if e["id"] == gw)
        if event.get("finished") and event.get("data_checked"):
            archive = f"leagues/{league_id}/gw/{gw}.json"
            # Frozen historical captures never get a new source timestamp.
            if archive not in files:
                put(archive, compact_snapshot(payload, finalized=True))
        available[str(league_id)] = sorted(int(n.split("/")[-1].removesuffix(".json")) for n in files
                                          if n.startswith(f"leagues/{league_id}/gw/"))
    entries = [json.loads(raw) for name, raw in files.items() if name.startswith(f"journal/{SEASON}/gw/")]
    put(f"journal/{SEASON}/index.json", build_index(entries, SEASON))
    files[f"journal/{SEASON}/gameweeks.csv"] = journal_csv(entries).encode()
    if any(len(raw) > MAX_FILE_BYTES for raw in files.values()) or sum(map(len, files.values())) > MAX_DATASET_BYTES:
        raise ValueError("Static publication exceeds size budget")
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())}
    release = hashlib.sha256(canonical(hashes)).hexdigest()
    manifest = {"schema_version": 1, "release": release, "revision": revision, "generated_at": captured_at,
                "source": "official-fpl-scheduled-snapshot", "max_age_hours": 12,
                "gameweeks": available, "files": hashes, "writes_enabled": False}
    return manifest, files


def write_dataset(output: Path, manifest: dict, files: dict[str, bytes]) -> None:
    root = output / "releases" / manifest["release"]
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (output / "manifest.json").write_bytes(canonical(manifest))
    (output / "index.html").write_text("<!doctype html><title>Fantasy Scout public data</title>"
                                     "<p>Scheduled public FPL snapshots. See <a href=manifest.json>manifest</a>.</p>")
    (output / ".nojekyll").touch()
