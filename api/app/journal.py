"""Season journal contracts and deterministic completed-GW aggregation."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JOURNAL_SCHEMA_VERSION = 1
DEFAULT_SEASON = "2026-27"


def record_hash(payload: dict[str, Any]) -> str:
    body = dict(payload)
    body.pop("record_hash", None)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def verify_record_hash(payload: dict[str, Any]) -> bool:
    return bool(payload.get("record_hash") and payload["record_hash"] == record_hash(payload))


def write_immutable_record(path: Path, payload: dict[str, Any]) -> bool:
    """Create a frozen record once; identical reruns are safe no-ops."""
    if not verify_record_hash(payload):
        raise ValueError("journal record hash is invalid")
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not verify_record_hash(existing):
            raise ValueError(f"existing journal record failed integrity verification: {path}")
        if existing == payload:
            return False
        raise FileExistsError(f"immutable journal record already exists with different content: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return True


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _manager(snapshot: dict[str, Any], team_id: int) -> dict[str, Any]:
    return next((row for row in snapshot.get("competitors", []) if int(row.get("entry_id") or 0) == team_id), {})


def _live_by_id(live: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(row["id"]): row.get("stats", {}) for row in live.get("elements", []) if row.get("id")}


def _prediction_metrics(rows: list[dict[str, Any]], actual: dict[int, dict[str, Any]], field: str) -> dict[str, Any]:
    pairs: list[tuple[float, float]] = []
    for row in rows:
        element = int(row.get("element") or row.get("id") or 0)
        if element in actual and row.get(field) is not None:
            pairs.append((_number(row[field]), _number(actual[element].get("total_points"))))
    if not pairs:
        return {"rows": 0, "mae": None, "bias": None}
    return {
        "rows": len(pairs),
        "mae": round(sum(abs(predicted - observed) for predicted, observed in pairs) / len(pairs), 3),
        "bias": round(sum(predicted - observed for predicted, observed in pairs) / len(pairs), 3),
    }


def build_gameweek_journal(
    *, season: str, gameweek: int, team_id: int, league_id: int,
    snapshot: dict[str, Any], analysis: dict[str, Any], live: dict[str, Any],
    predeadline: dict[str, Any] | None = None, public_lesson: str | None = None,
) -> dict[str, Any]:
    manager = _manager(snapshot, team_id)
    if not manager:
        raise ValueError(f"Team {team_id} is missing from league {league_id} GW{gameweek}")
    actual = _live_by_id(live)
    squad = []
    for pick in manager.get("squad", []):
        stats = actual.get(int(pick.get("element") or 0), {})
        squad.append({
            "element": pick.get("element"), "name": pick.get("name"), "team": pick.get("team"),
            "position": pick.get("position"), "multiplier": pick.get("multiplier", 0),
            "is_captain": bool(pick.get("is_captain")), "is_vice_captain": bool(pick.get("is_vice_captain")),
            "points": int(stats.get("total_points") or 0), "minutes": int(stats.get("minutes") or 0),
        })
    captain = next((row for row in squad if row["is_captain"]), None)
    elite_average = _number((predeadline or {}).get("decision", {}).get("elite_average_points"))
    if not elite_average:
        elite_average = _number(analysis.get("squad_ownership", {}).get("avg_gw_points"))
    gw_points = int(manager.get("gw_points") or 0)
    decision = (predeadline or {}).get("decision", {})
    v5_rows = (predeadline or {}).get("v5", {}).get("players", [])
    fpl_rows = (predeadline or {}).get("fpl_baseline", [])
    quality_issues: list[str] = []
    if not predeadline:
        quality_issues.append("predeadline_evidence_missing")
    if not live.get("elements"):
        quality_issues.append("official_live_results_missing")
    lessons = []
    delta = round(gw_points - elite_average, 1)
    lessons.append(f"Scored {abs(delta):.1f} points {'above' if delta >= 0 else 'below'} the league reference.")
    if captain:
        lessons.append(f"Captain {captain['name']} returned {captain['points']} points before multiplier.")
    if int(manager.get("gw_transfers_cost") or 0):
        lessons.append(f"Transfer hits cost {int(manager.get('gw_transfers_cost') or 0)} points.")
    generated_at = str(snapshot.get("fetched_at") or datetime.now(timezone.utc).isoformat())
    result = {
        "schema_version": JOURNAL_SCHEMA_VERSION, "season": season, "gameweek": gameweek,
        "status": "final", "generated_at": generated_at, "team_id": team_id, "league_id": league_id,
        "summary": {
            "gw_points": gw_points, "total_points": int(manager.get("total_points") or 0),
            "overall_rank": int(manager.get("overall_rank") or manager.get("rank") or 0),
            "league_rank": int(manager.get("league_rank") or 0), "elite_average": round(elite_average, 1),
            "points_vs_reference": delta, "captain": captain.get("name") if captain else manager.get("captain"),
            "captain_points": captain.get("points") if captain else None,
            "transfers": int(manager.get("transfers_made") or manager.get("gw_transfers") or 0),
            "hit_cost": int(manager.get("gw_transfers_cost") or 0), "chip": manager.get("active_chip"),
            "phase": decision.get("competitive", {}).get("phase"),
            "alignment": decision.get("competitive", {}).get("alignment"),
        },
        "decision": {
            "captured": bool(predeadline), "decision_id": decision.get("decision_id"),
            "model_version": decision.get("model_version", "competitive-v4.0"),
            "packet_status": decision.get("packet_status"), "competitive": decision.get("competitive", {}),
            "plan": decision.get("plan"), "v5_projection_version": (predeadline or {}).get("v5", {}).get("projection_version"),
        },
        "outcome": {"squad": squad, "transfers": manager.get("transfer_details", []), "chips": manager.get("chips_used", [])},
        "league": {
            "competitors": int(analysis.get("total_competitors") or snapshot.get("total_entries") or 0),
            "top_owned": analysis.get("squad_ownership", {}).get("top_owned", [])[:10],
            "captain_choices": analysis.get("squad_ownership", {}).get("captain_choices", [])[:8],
            "formations": analysis.get("formations", [])[:8], "transfer_trends": analysis.get("transfers", {}),
            "chips": analysis.get("chips", {}),
        },
        "evaluation": {
            "v5": _prediction_metrics(v5_rows, actual, "xpts_mean"),
            "fpl_ep_next": _prediction_metrics(fpl_rows, actual, "ep_next"),
            "horizons": [],
        },
        "learning": {"automated": lessons, "public_lesson": public_lesson},
        "quality": {"status": "valid" if not quality_issues else "partial", "issues": quality_issues},
        "provenance": {
            "snapshot_at": snapshot.get("fetched_at"), "analysis_at": analysis.get("generated_at"),
            "predeadline_at": (predeadline or {}).get("captured_at"),
            "sources": ["official-fpl", "league-snapshot", "competitive-v4", "projection-v5-lab"],
        },
    }
    result["record_hash"] = record_hash(result)
    return result


def build_index(entries: list[dict[str, Any]], season: str) -> dict[str, Any]:
    ordered = sorted(entries, key=lambda row: int(row["gameweek"]))
    return {
        "schema_version": JOURNAL_SCHEMA_VERSION, "season": season,
        "updated_at": ordered[-1]["generated_at"] if ordered else None,
        "gameweeks": [{"gameweek": row["gameweek"], "status": row["status"], "summary": row["summary"],
                       "quality": row["quality"], "record_hash": row["record_hash"]} for row in ordered],
        "totals": {"completed": len(ordered), "points": ordered[-1]["summary"]["total_points"] if ordered else 0},
    }


def journal_csv(entries: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    fields = ["season", "gameweek", "gw_points", "total_points", "overall_rank", "league_rank",
              "elite_average", "points_vs_reference", "captain", "captain_points", "transfers", "hit_cost", "chip", "phase", "alignment", "quality"]
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in sorted(entries, key=lambda item: int(item["gameweek"])):
        writer.writerow({"season": row["season"], "gameweek": row["gameweek"], **row["summary"], "quality": row["quality"]["status"]})
    return output.getvalue()


def read_journal_entries(data_dir: Path, season: str) -> list[dict[str, Any]]:
    root = data_dir / "journal" / season
    entries = []
    for path in sorted(root.glob("gw*.json")):
        try:
            entries.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return entries
