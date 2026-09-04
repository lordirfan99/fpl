from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import monotonic, sleep
from typing import Any

import httpx


FPL_BASE = "https://fantasy.premierleague.com/api"
_cache: dict[str, tuple[float, Any]] = {}


def _get(path: str, ttl: int = 30) -> Any:
    now = monotonic()
    cached = _cache.get(path)
    if cached and cached[0] > now:
        return cached[1]
    response = httpx.get(
        f"{FPL_BASE}/{path.lstrip('/')}",
        headers={"User-Agent": "FPLScoutLive/1.0", "Accept": "application/json"},
        timeout=20,
        follow_redirects=True,
    )
    response.raise_for_status()
    value = response.json()
    _cache[path] = (now + ttl, value)
    return value


def current_gameweek() -> int:
    payload = _get("bootstrap-static/", ttl=30)
    current = next((event for event in payload.get("events", []) if event.get("is_current")), None)
    return int((current or {}).get("id") or 1)


def team(entry_id: int, gameweek: int, league_id: int | None = None) -> dict[str, Any]:
    """Read the public, mutable FPL team state for one entry.

    This is intentionally a live read model. It is never used to rewrite the
    journal or any completed-gameweek snapshot.
    """
    bootstrap = _get("bootstrap-static/", ttl=30)
    players = {int(row["id"]): row for row in bootstrap.get("elements", [])}
    entry = _get(f"entry/{entry_id}/", ttl=30)
    picks_payload = _get(f"entry/{entry_id}/event/{gameweek}/picks/", ttl=15)
    history = _get(f"entry/{entry_id}/history/", ttl=30)
    fixtures = _get(f"fixtures/?event={gameweek}", ttl=30)
    history_row = next((row for row in history.get("current", []) if int(row.get("event") or 0) == gameweek), {})
    picks: list[dict[str, Any]] = []
    for pick in picks_payload.get("picks", []):
        player = players.get(int(pick.get("element") or 0), {})
        picks.append({
            "element": int(pick.get("element") or 0),
            "position": int(pick.get("position") or 0),
            "multiplier": int(pick.get("multiplier") or 0),
            "is_captain": bool(pick.get("is_captain")),
            "is_vice_captain": bool(pick.get("is_vice_captain")),
            "web_name": player.get("web_name", "Unknown"),
            "team": int(player.get("team") or 0),
            "points": int(player.get("event_points") or 0),
            "now_cost": int(player.get("now_cost") or 0),
        })
    live_points = history_row.get("event_total")
    if live_points is None:
        # During a live Gameweek FPL does not always publish the history row
        # yet. The official player event totals and the saved pick
        # multipliers still provide the manager's current provisional score.
        live_points = sum(pick["points"] * pick["multiplier"] for pick in picks)
    classic_leagues = entry.get("leagues", {}).get("classic", [])
    league = next((row for row in classic_leagues if int(row.get("id") or 0) == league_id), None)
    # The upcoming GW's picks stay private until its deadline, so `active_chip`
    # is only populated once the deadline has passed. `chips` is the permanent
    # record of every chip already played.
    active_chip = picks_payload.get("active_chip") or None
    chips_played = [
        {"name": row.get("name"), "event": row.get("event")}
        for row in (history.get("chips") or [])
        if row.get("name")
    ]
    return {
        "source": "official-fpl-live",
        "status": "live",
        "gameweek": gameweek,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "active_chip": active_chip,
        "chips_played": chips_played,
        "entry": {
            "id": int(entry_id),
            "entry_name": entry.get("name", ""),
            "player_name": entry.get("player_first_name", "") + " " + entry.get("player_last_name", ""),
            "overall_rank": history_row.get("overall_rank") or entry.get("summary_overall_rank") or 0,
            "total_points": history_row.get("total_points") or entry.get("summary_overall_points") or 0,
            "value": entry.get("last_deadline_value") or entry.get("value") or 0,
            "bank": entry.get("last_deadline_bank") or entry.get("bank") or 0,
            "transfers_made": history_row.get("event_transfers") or 0,
            "transfers_cost": history_row.get("event_transfers_cost") or 0,
        },
        "league": ({
            "id": int(league.get("id") or 0),
            "name": league.get("name", ""),
            "entry_rank": int(league.get("entry_rank") or 0),
            "entry_last_rank": int(league.get("entry_last_rank") or 0),
            "rank_count": int(league.get("rank_count") or 0),
        } if league else None),
        "picks": picks,
        "points": int(live_points),
        "points_source": "history" if history_row.get("event_total") is not None else "official-live-picks",
        "provisional": not bool((next((e for e in bootstrap.get("events", []) if int(e.get("id") or 0) == gameweek), {}) or {}).get("finished")),
        # These are the official fixtures for this GW.  The frontend uses
        # them to show each player's points once a match starts, or their
        # upcoming opponent before kick-off.
        "fixtures": [{
            "team_h": int(fixture.get("team_h") or 0),
            "team_a": int(fixture.get("team_a") or 0),
            "started": bool(fixture.get("started")),
            "finished": bool(fixture.get("finished")),
            "kickoff_time": fixture.get("kickoff_time"),
        } for fixture in fixtures],
    }


def league_standings(league_id: int) -> dict[str, Any]:
    """Read the current classic-league standings directly from FPL.

    FPL publishes event totals and ranks while a gameweek is live, before our
    immutable research snapshot is finalized. Keep this read model separate
    from snapshot collection so provisional values can never overwrite history.
    """
    rows: list[dict[str, Any]] = []
    seen_entries: set[int] = set()
    page = 1
    # Classic-league pages contain 50 managers.  Fetch every page FPL says is
    # available: a live view must never silently turn a large league into a
    # top-200 sample.  The upper bound is only a circuit breaker for a broken
    # upstream pagination response (12,500 managers is far above this app's
    # supported league size).
    max_pages = 250
    while page <= max_pages:
        payload = _get(
            f"leagues-classic/{league_id}/standings/?page_standings={page}&page_new_entries=1",
            ttl=60,
        )
        standings = payload.get("standings", {})
        batch = standings.get("results", [])
        if not batch:
            break
        for row in batch:
            entry_id = int(row.get("entry") or 0)
            if entry_id and entry_id not in seen_entries:
                seen_entries.add(entry_id)
                rows.append(row)
        if not standings.get("has_next"):
            break
        page += 1
    else:
        raise RuntimeError(f"FPL standings pagination exceeded {max_pages} pages for league {league_id}")
    return {
        "source": "official-fpl-live",
        "status": "live",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "league_id": league_id,
        "count": len(rows),
        "pages_fetched": page,
        "managers": rows,
    }


def hydrate_manager_squads(rows: list[dict[str, Any]], gameweek: int, limit: int) -> int:
    """Hydrate the top cohort's squads from official FPL picks in parallel."""
    bootstrap = _get("bootstrap-static/", ttl=30)
    players = {int(row["id"]): row for row in bootstrap.get("elements", [])}
    teams = {int(row["id"]): row.get("name", "—") for row in bootstrap.get("teams", [])}
    targets = rows[:limit]

    def hydrate(row: dict[str, Any]) -> tuple[int, list[dict[str, Any]], str, dict[str, Any]]:
        empty_extras: dict[str, Any] = {"bank": None, "overall_rank": None, "event_transfers": None}
        for attempt in range(3):
            try:
                payload = _get(f"entry/{int(row['entry'])}/event/{gameweek}/picks/", ttl=30)
                break
            except Exception:
                if attempt == 2:
                    return int(row["entry"]), [], "", empty_extras
                sleep(0.25 * (attempt + 1))
        try:
            picks = []
            captain = ""
            for pick in payload.get("picks", []):
                player = players.get(int(pick.get("element") or 0), {})
                selection_position = int(pick.get("position") or 99)
                element_type = int(player.get("element_type") or 3)
                position = "GKP" if element_type == 1 else "DEF" if element_type == 2 else "MID" if element_type == 3 else "FWD"
                name = player.get("web_name", "Unknown")
                if pick.get("is_captain"):
                    captain = name
                # The public FPL payload can mark every pick with a positive
                # multiplier. Selection order is the reliable representation
                # of the submitted XI: positions 1–11 start, 12–15 are bench.
                multiplier = (2 if pick.get("is_captain") else 1) if selection_position <= 11 else 0
                picks.append({"element": int(pick.get("element") or 0), "name": name, "position": position, "team": teams.get(int(player.get("team") or 0), "—"), "cost": int(player.get("now_cost") or 0) / 10, "multiplier": multiplier, "is_captain": bool(pick.get("is_captain")), "is_vice_captain": bool(pick.get("is_vice_captain")), "selected_by": float(player.get("selected_by_percent") or 0)})
            # The same picks payload carries the manager's live bank, official
            # overall rank and GW transfer count in `entry_history`. Capture
            # them here so the published snapshot is a complete recommendation
            # input without any extra FPL request.
            history = payload.get("entry_history") or {}

            def _as_int(value: Any) -> int | None:
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            extras = {
                "bank": _as_int(history.get("bank")),
                "overall_rank": _as_int(history.get("overall_rank")) or None,
                "event_transfers": _as_int(history.get("event_transfers")),
            }
            return int(row["entry"]), picks, captain, extras
        except Exception:
            return int(row["entry"]), [], "", empty_extras

    hydrated = 0
    # A public league's five-percent cohort can be 90+ squads.  Eight workers
    # made the live Elite route exceed the dashboard's render budget even
    # though the FPL calls were healthy.  Sixteen keeps requests bounded while
    # allowing the factual full-cohort response to arrive in time.
    with ThreadPoolExecutor(max_workers=max(1, min(16, len(targets)))) as pool:
        futures = [pool.submit(hydrate, row) for row in targets]
        results = [future.result() for future in as_completed(futures)]
    by_id = {entry_id: (picks, captain, extras) for entry_id, picks, captain, extras in results}
    for row in rows:
        entry_id = int(row.get("entry") or 0)
        if entry_id in by_id and by_id[entry_id][0]:
            picks, captain, extras = by_id[entry_id]
            row["_live_squad"] = picks
            row["_live_captain"] = captain
            row["_live_bank"] = extras.get("bank")
            row["_live_overall_rank"] = extras.get("overall_rank")
            row["_live_event_transfers"] = extras.get("event_transfers")
            hydrated += 1
    return hydrated
