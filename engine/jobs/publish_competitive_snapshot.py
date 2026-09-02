"""Publish the VM's refreshed competitive cohort to private Cloud Storage.

The payload matches the Scout API's existing snapshot contract. It is sampled
to the bounded deep cohort, while ``population_size`` preserves the league-size
denominator used for elite-percentile selection.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "model"))

from atomic_io import atomic_write_json  # noqa: E402
from fpl_client import FPLClient  # noqa: E402

STATE_FILE = os.path.join(BASE, "data", "processed", "league_intelligence", "latest.json")
OUT_DIR = os.path.join(BASE, "data", "processed", "competitive_snapshots")
DEFAULT_BUCKET = "irfan-374115-fpl-snapshots"
DEFAULT_API = "https://fpl-scout-api-bztsnhv3ea-uc.a.run.app"
POSITION = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def _load(path):
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def _publisher_token():
    token = os.getenv("FPL_SNAPSHOT_PUBLISH_TOKEN", "").strip()
    if token:
        return token
    path = os.getenv("FPL_DASHBOARD_ENV_FILE", "/etc/fpl-autopilot-dashboard.env")
    try:
        with open(path, encoding="utf-8") as source:
            for line in source:
                if line.startswith("DASHBOARD_READ_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    raise RuntimeError("snapshot publisher token is unavailable")


def _manager(entry, membership, payload, history, transfers, elements, teams, league_id, fetched_at):
    picks = sorted(payload.get("picks") or [], key=lambda row: int(row.get("position") or 99))
    squad = []
    for pick in picks:
        element = elements.get(int(pick["element"]), {})
        squad.append({
            "element": int(pick["element"]),
            "name": element.get("web_name", str(pick["element"])),
            "position": POSITION.get(int(element.get("element_type") or 0), "?"),
            "team": teams.get(int(element.get("team") or 0), str(element.get("team") or "?")),
            "cost": float(element.get("now_cost") or 0) / 10,
            "is_captain": bool(pick.get("is_captain")),
            "is_vice_captain": bool(pick.get("is_vice_captain")),
            "multiplier": int(pick.get("multiplier") or 0),
            "selected_by": float(element.get("selected_by_percent") or 0),
        })
    event_history = payload.get("entry_history") or {}
    current = history.get("current") or []
    latest = current[-1] if current else {}
    event = int(event_history.get("event") or latest.get("event") or 0)
    event_transfers = [row for row in (transfers or []) if int(row.get("event") or -1) == event]
    return {
        "entry_id": int(entry), "gw": event, "fetched_at": fetched_at,
        "gw_points": int(event_history.get("points") or latest.get("points") or 0),
        "total_points": int(event_history.get("total_points") or latest.get("total_points") or 0),
        "overall_rank": int(event_history.get("overall_rank") or latest.get("overall_rank") or 0),
        "gw_transfers": int(event_history.get("event_transfers") or latest.get("event_transfers") or 0),
        "gw_transfers_cost": int(event_history.get("event_transfers_cost") or latest.get("event_transfers_cost") or 0),
        "chips_used": history.get("chips") or [], "squad": squad,
        "captain": next((p["name"] for p in squad if p["is_captain"]), None),
        "vice_captain": next((p["name"] for p in squad if p["is_vice_captain"]), None),
        "active_chip": payload.get("active_chip"),
        "entry_name": membership.get("entry_name"), "player_name": membership.get("player_name"),
        "league_id": int(league_id), "league_rank": membership.get("rank"),
        "transfers_made": len(event_transfers), "transfer_details": event_transfers,
    }


def main():
    state = _load(STATE_FILE)
    target_gw = int(state["event"])
    exposure_gw = int(state.get("exposure_event") or 0)
    if exposure_gw <= 0:
        raise RuntimeError("no locked gameweek picks available for competitive snapshot")
    api = os.getenv("FPL_COMPETITIVE_API_URL", DEFAULT_API).rstrip("/")
    token = _publisher_token()
    client = FPLClient(session_data={})
    bootstrap = client.get_json("bootstrap-static/")
    fixtures = client.get_json("fixtures/")
    elements = {int(row["id"]): row for row in bootstrap.get("elements", [])}
    teams = {int(row["id"]): row.get("name", str(row["id"])) for row in bootstrap.get("teams", [])}
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    standings = state.get("standings") or []
    cohort_ids = {int(row["entry"]) for row in (state.get("cohort") or [])}
    cohort_ids.add(int(state["our_entry"]))
    os.makedirs(OUT_DIR, exist_ok=True)
    published = []

    for league_id in state.get("league_ids") or []:
        memberships = {int(row["entry"]): row for row in standings if int(row["league_id"]) == int(league_id)}
        competitors, errors = [], []
        for entry in sorted(cohort_ids & set(memberships)):
            try:
                picks = client.entry_picks(entry, exposure_gw)
                history = client.entry_history(entry)
                transfers = client.entry_transfers(entry)
                manager = _manager(entry, memberships[entry], picks, history, transfers,
                                   elements, teams, league_id, fetched_at)
                if len(manager["squad"]) == 15:
                    competitors.append(manager)
                else:
                    errors.append({"entry": entry, "reason": "incomplete_squad"})
            except Exception as exc:
                errors.append({"entry": entry, "reason": repr(exc)[:120]})
        if not any(row["entry_id"] == int(state["our_entry"]) for row in competitors):
            raise RuntimeError(f"our team missing from league {league_id} snapshot")
        if errors:
            raise RuntimeError(f"league {league_id} snapshot incomplete: {len(errors)} cohort fetch errors")
        payload = {
            "gw": target_gw, "exposure_gw": exposure_gw, "league_id": int(league_id),
            "run_id": os.getenv("FPL_RUN_ID") or state.get("run_id"),
            "fetched_at": fetched_at, "total_entries": len(competitors),
            "population_size": len(memberships), "sampled": True,
            "errors": [], "competitors": competitors,
        }
        filename = f"gw{target_gw}_league{int(league_id)}_data.json"
        path = os.path.join(OUT_DIR, filename)
        atomic_write_json(path, payload)
        published.append(path)

    atomic_write_json(os.path.join(OUT_DIR, "bootstrap_cache.json"), bootstrap)
    gameweeks = {}
    for fixture in fixtures:
        event = fixture.get("event")
        if event is None:
            continue
        gameweeks.setdefault(str(event), []).append({
            "event": int(event), "team_h": teams.get(int(fixture["team_h"]), str(fixture["team_h"])),
            "team_a": teams.get(int(fixture["team_a"]), str(fixture["team_a"])),
            "team_h_difficulty": fixture.get("team_h_difficulty"),
            "team_a_difficulty": fixture.get("team_a_difficulty"),
            "kickoff_time": fixture.get("kickoff_time"),
        })
    fixtures_path = os.path.join(OUT_DIR, "fixtures_cache.json")
    atomic_write_json(fixtures_path, {"fetched_at": fetched_at, "source": "official-fpl-api", "gameweeks": gameweeks})
    published.extend([os.path.join(OUT_DIR, "bootstrap_cache.json"), fixtures_path])

    for path in published:
        with open(path, "rb") as source:
            body = source.read()
        request = urllib.request.Request(
            f"{api}/internal/v1/snapshots/{os.path.basename(path)}",
            data=body, method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "User-Agent": "fpl-autopilot/snapshot-publisher"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                if response.status != 200:
                    raise RuntimeError(f"snapshot API returned HTTP {response.status}")
        except Exception as exc:
            raise RuntimeError(f"snapshot publish failed for {os.path.basename(path)}: {exc!r}") from exc
    print(f"published {len(published)} files through the private snapshot API for GW{target_gw}")


if __name__ == "__main__":
    main()
