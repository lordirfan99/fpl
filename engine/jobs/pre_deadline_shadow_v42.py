#!/usr/bin/env python3
"""Generate a non-executable Competitive V4.2 candidate packet."""
import datetime
import hashlib
import json
import os
import sys
import urllib.request

from project_paths import resolve_project_root

BASE = str(resolve_project_root(__file__))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "optimizer"))
sys.path.insert(0, os.path.join(BASE, "execution"))

from calibration_v42 import calibration_summary
from feature_store_v42 import history_by_player, load_event_history, team_strengths
from fixture_engine import fixtures_by_team_gw
from fpl_client import FPLClient
from horizon_milp import optimize_horizon
from v42_projection import POS_MAP, project_player_v42

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


def fetch(url, timeout=90):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def load_settings():
    with open(os.path.join(BASE, "config", "settings.json"), encoding="utf-8") as handle:
        return json.load(handle)


def fingerprint(bootstrap, fixtures, gw):
    payload = {
        "gw": gw,
        "deadline": next((e.get("deadline_time") for e in bootstrap["events"]
                          if int(e.get("id") or 0) == gw), None),
        "players": [[p.get("id"), p.get("now_cost"), p.get("status"), p.get("news_added"),
                     p.get("minutes"), p.get("starts")] for p in bootstrap["elements"]],
        "fixtures": [[f.get("id"), f.get("event"), f.get("kickoff_time"),
                      f.get("team_h"), f.get("team_a")] for f in fixtures],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    settings = load_settings()
    bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    fixtures = fetch("https://fantasy.premierleague.com/api/fixtures/")
    next_event = None
    for event in bootstrap.get("events", []):
        deadline = datetime.datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
        if not event.get("finished") and deadline > now:
            next_event = event
            break
    if not next_event:
        print("V4.2 shadow: no upcoming gameweek")
        return 0

    gw = int(next_event["id"])
    gw_ids = list(range(gw, min(39, gw + 3)))
    history_path = os.path.join(BASE, "data", "processed", "player_event_history.jsonl")
    history = history_by_player(load_event_history(history_path), gw)
    strengths = team_strengths(fixtures, gw)
    fmap = fixtures_by_team_gw(fixtures, gw_ids)
    calibration = calibration_summary(
        os.path.join(BASE, "data", "processed", "v42_residuals.csv")
    )

    players = []
    for element in bootstrap.get("elements", []):
        if not element.get("can_select"):
            continue
        forecast = project_player_v42(element, fmap, gw_ids, history, strengths, calibration)
        players.append({
            "id": int(element["id"]), "name": element["web_name"],
            "position": POS_MAP.get(int(element.get("element_type") or 3), "MID"),
            "club": int(element["team"]), "cost": int(element["now_cost"]),
            "status": element.get("status"), "news": element.get("news"),
            "xpts": forecast["mean"], "xpts_floor": forecast["floor"],
            "xpts_upside": forecast["upside"], "xpts_variance": forecast["variance"],
            "xpts_by_gw": forecast["xpts_by_gw"],
            "variance_by_gw": forecast["variance_by_gw"],
            "xpts_horizon": forecast["expected_horizon"],
            **{key: forecast[key] for key in (
                "p_dnp", "p_1_59", "p_60_plus", "p_start", "expected_minutes",
                "confidence", "signals", "components", "calibration_sample",
                "degraded_reasons")},
        })

    by_id = {p["id"]: p for p in players}
    team = FPLClient().my_team(int(settings["team_id"]))
    squad = []
    for pick in team.get("picks", []):
        player = by_id.get(int(pick["element"]))
        if not player:
            continue
        owned = dict(player)
        owned["selling_price"] = int(pick.get("selling_price") or player["cost"])
        owned["purchase_price"] = int(pick.get("purchase_price") or player["cost"])
        squad.append(owned)
    transfers = team.get("transfers") or {}
    free_transfers = 5 if transfers.get("status") == "unlimited" else max(
        0, int(transfers.get("limit") or 1) - int(transfers.get("made") or 0)
    )
    optimizer = None
    optimizer_error = None
    try:
        optimizer = optimize_horizon(
            squad, players, int(transfers.get("bank") or 0), free_transfers,
            horizon=len(gw_ids), weights=(1.0, 0.7, 0.5),
            risk_penalty=float(settings.get("v4_transfer_risk_penalty", 0.25)),
            bench_weight=float(settings.get("v4_bench_depth_weight", 0.08)),
            max_transfers_per_gw=int(settings.get("v4_joint_transfer_limit", 2)),
            max_paid_transfers=int(settings.get("v4_max_paid_transfers", 1)),
            paid_transfers_allowed=(gw - 1 >= int(settings.get("v4_paid_transfer_min_gws", 3))),
            captain_min_start=float(settings.get("v4_captain_min_start", 0.75)),
            captain_min_minutes=float(settings.get("v4_captain_min_minutes", 65)),
            transfer_friction=float(settings.get("v4_transfer_friction", 0.15)),
        )
    except Exception as error:
        optimizer_error = f"{type(error).__name__}: {error}"[:300]

    first_week = ((optimizer or {}).get("weeks") or [{}])[0]
    candidate = {
        "schema_version": 1,
        "artifact_type": "non_executable_shadow",
        "model_version": "competitive-v4.2-shadow",
        "champion_version": "competitive-v4.0",
        "optimizer_version": "v4.1-shadow" if optimizer else None,
        "gw": gw, "generated_at": now.isoformat(),
        "feature_as_of": now.isoformat(), "deadline": next_event["deadline_time"],
        "source_fingerprint": fingerprint(bootstrap, fixtures, gw),
        "official_fpl_only": True, "betting_odds_used": False,
        "calibration": calibration,
        "history_rows": sum(len(rows) for rows in history.values()),
        "players": players,
        "squad_ids": [p["id"] for p in squad],
        "captain_id": first_week.get("captain_id"),
        "lineup_ids": first_week.get("lineup_ids") or [],
        "first_week": first_week,
        "optimizer": optimizer,
        "optimizer_error": optimizer_error,
    }
    path = os.path.join(BASE, "data", "processed", f"v42_shadow_gw{gw}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(candidate, handle, indent=1)
    os.replace(tmp, path)
    print(f"V4.2 shadow GW{gw}: {len(players)} players, {candidate['history_rows']} history rows, "
          f"optimizer={'ready' if optimizer else 'degraded'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
