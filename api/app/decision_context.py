"""Recorded league facts, not forecasts or authenticated account information."""
import math
import threading
import time
from collections import Counter
from datetime import datetime

from .live_freshness import snapshot_freshness
from .repository import LiveSnapshotNotFoundError, SnapshotNotFoundError

_cache = {}
_lock = threading.Lock()


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def source_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return str(value) if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def valid_manager_rows(managers, population):
    if not isinstance(managers, list) or not positive_int(population):
        return []
    return [m for m in managers if isinstance(m, dict) and positive_int(m.get("entry_id"))
            and positive_int(m.get("league_rank")) and number(m.get("total_points"))]


def goal(managers, population, owner):
    valid = valid_manager_rows(managers, population)
    complete = (positive_int(population) and positive_int(owner) and len(valid) == population
                and len({m["entry_id"] for m in valid}) == population)
    if not complete:
        return {"available": False, "reason": "incomplete_standings", "manager_count": population}
    ordered = sorted(valid, key=lambda m: (m["league_rank"], m["entry_id"]))
    own = next((m for m in valid if m["entry_id"] == owner), None)
    if own is None:
        return {"available": False, "reason": "owner_not_in_league", "manager_count": population}
    cutoff_rank = math.ceil(population * .10)
    cutoff = ordered[cutoff_rank - 1]["total_points"]
    return {"available": True, "manager_count": population, "cutoff_rank": cutoff_rank,
            "cutoff_points": cutoff, "owner_points": own["total_points"], "owner_rank": own["league_rank"],
            "points_gap": cutoff - own["total_points"], "tied_cutoff": own["total_points"] == cutoff,
            "inside_target": own["league_rank"] <= cutoff_rank}


def ownership(managers, population, owner):
    if not positive_int(population):
        return {"rows": [], "sample_count": 0, "population": population, "cohort_count": 0, "cohort_sample": 0}
    cutoff_rank = math.ceil(population * .10)
    ranked = sorted(valid_manager_rows(managers, population), key=lambda m: (m["league_rank"], m["entry_id"]))
    # Official tied ranks can make the target group larger than ceil(N * 10%).
    # The threshold remains that exact rank; every manager officially at or
    # above it belongs to the recorded comparison cohort.
    cohort = {m["entry_id"] for m in ranked if m["league_rank"] <= cutoff_rank}

    def complete_squad(manager):
        squad = manager.get("squad")
        return (isinstance(squad, list) and len(squad) == 15
                and all(isinstance(p, dict) and positive_int(p.get("element")) for p in squad)
                and len({p["element"] for p in squad}) == 15)

    hydrated = [m for m in ranked if complete_squad(m)]
    target = [m for m in hydrated if m["entry_id"] in cohort]
    all_owned, target_owned, captains = Counter(), Counter(), Counter()
    player_info = {}
    own = next((m for m in hydrated if m["entry_id"] == owner), None)
    own_ids = {p["element"] for p in own["squad"]} if own else None
    for manager in hydrated:
        for p in manager["squad"]:
            element = p["element"]
            player_info.setdefault(element, {"element": element, "name": p.get("name") or f"Player {element}",
                                              "position": p.get("position"), "team": p.get("team")})
            all_owned[element] += 1
            if manager["entry_id"] in cohort:
                target_owned[element] += 1
                if p.get("is_captain") is True:
                    captains[element] += 1
    rows = [{**player_info[element], "league_pct": round(100 * count / len(hydrated), 1),
             "target_pct": round(100 * target_owned[element] / len(target), 1) if target else None,
             "target_captain_pct": round(100 * captains[element] / len(target), 1) if target else None,
             "owned_at_snapshot": element in own_ids if own_ids is not None else None}
            for element, count in all_owned.items()]
    return {"rows": sorted(rows, key=lambda r: (-(r["target_pct"] or 0), -r["league_pct"], r["element"])),
            "sample_count": len(hydrated), "population": population, "cohort_rank_threshold": cutoff_rank,
            "cohort_count": len(cohort), "cohort_sample": len(target)}


def build_context(repo, league_id, owner):
    # Bounded per-league cache keeps full archived squad payloads off the browser
    # and prevents every refresh from downloading historical files again.
    key = (league_id, owner)
    with _lock:
        saved = _cache.get(key)
        if saved and time.monotonic() - saved[0] < 300:
            return saved[1]
    try:
        live = repo.live_league(league_id)
    except LiveSnapshotNotFoundError:
        return {"schema_version": 1, "league_id": league_id, "status": "unavailable", "goal": {"available": False}, "history": [], "ownership": {"rows": []}}
    managers = live.get("managers") or []
    current_population = live.get("expected_count")
    target = goal(managers, current_population, owner)
    fresh = snapshot_freshness(live.get("captured_at"))
    current_recorded_at = source_timestamp(live.get("captured_at"))
    current_gw = int(live.get("gameweek") or 0)
    history = []
    try:
        completed = {e["id"] for e in repo.bootstrap().get("events", []) if e.get("finished") and e.get("data_checked")}
    except SnapshotNotFoundError:
        completed = set()
    for gw in range(max(1, current_gw - 6), current_gw + 1):
        row = {"gameweek": gw, "points_gap": None, "snapshot_at": None}
        if gw == current_gw and target["available"] and current_recorded_at:
            row.update(points_gap=target["points_gap"], snapshot_at=current_recorded_at)
        elif gw in completed:
            try:
                archive = repo.league(league_id, gw)
                archive_population = archive.get("population_size") or archive.get("total_entries")
                historical = goal(archive.get("competitors") or [], archive_population, owner)
                recorded_at = source_timestamp(archive.get("fetched_at"))
                if historical["available"] and recorded_at:
                    row.update(points_gap=historical["points_gap"], snapshot_at=recorded_at)
            except SnapshotNotFoundError:
                pass
        history.append(row)
    result = {"schema_version": 1, "league_id": league_id, "gameweek": current_gw,
              "status": "historical" if fresh["stale"] else "ready", "snapshot_at": live.get("captured_at"),
              "freshness": fresh, "goal": target, "history": history,
              "ownership": ownership(managers, current_population, owner) if target["available"] else {"rows": []},
              "source": "official-fpl-live", "scope": "public_gameweek_research", "writes_enabled": False}
    with _lock:
        if len(_cache) >= 8:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), result)
    return result
