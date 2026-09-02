#!/usr/bin/env python3
"""Shadow-run Intelligence Engine v3 without changing the live FPL team.

Writes data/processed/v3_shadow_gwN.json. No Telegram and no FPL POSTs.
This lets v3 collect real decisions/metrics before becoming authoritative.
"""
import datetime
import json
import os
import sys
import urllib.request

from project_paths import resolve_project_root

BASE = str(resolve_project_root(__file__))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "optimizer"))
sys.path.insert(0, os.path.join(BASE, "execution"))

from fixture_engine import fixtures_by_team_gw
from component_xpts import gameweek_xpts, POS_MAP
from multigw_planner import plan_sequences
from calibration import calibration_summary
from fpl_client import FPLClient

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
WEIGHTS = [1.0, 0.75, 0.55, 0.4]


def fetch(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def load_settings():
    with open(os.path.join(BASE, "config", "settings.json"), encoding="utf-8") as f:
        return json.load(f)


def validate_bootstrap(d):
    for k in ("elements", "events", "teams"):
        if k not in d or not isinstance(d[k], list) or not d[k]:
            raise ValueError(f"bootstrap invalid/missing {k}")


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    settings = load_settings()
    team_id = settings["team_id"]
    bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    validate_bootstrap(bootstrap)
    fixtures = fetch("https://fantasy.premierleague.com/api/fixtures/")

    next_gw = None
    for ev in bootstrap["events"]:
        dl = datetime.datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
        if not ev.get("finished") and dl > now:
            next_gw = ev
            break
    if not next_gw:
        return 0

    gw = next_gw["id"]
    gw_so_far = max(0, gw - 1)
    gw_ids = list(range(gw, min(39, gw + 4)))
    fmap = fixtures_by_team_gw(fixtures, gw_ids)

    players = []
    for el in bootstrap["elements"]:
        if not el.get("can_select"):
            continue
        pos = POS_MAP.get(el.get("element_type"), "MID")
        forecasts = []
        for g in gw_ids:
            fc = gameweek_xpts(el, fmap.get((g, el["team"]), []), gw_so_far)
            forecasts.append(fc)
        x_by_gw = [f.mean for f in forecasts]
        v_by_gw = [f.variance for f in forecasts]
        horizon = sum(WEIGHTS[i] * x for i, x in enumerate(x_by_gw))
        f0 = forecasts[0]
        players.append({
            "id": el["id"], "name": el["web_name"], "position": pos,
            "club": el["team"], "cost": int(el["now_cost"]),
            "xpts": f0.mean, "xpts_floor": f0.floor, "xpts_upside": f0.upside,
            "xpts_variance": f0.variance, "p_start": f0.p_start,
            "expected_minutes": f0.expected_minutes,
            "components": f0.components,
            "xpts_by_gw": x_by_gw, "variance_by_gw": v_by_gw,
            "xpts_horizon": round(horizon, 3),
        })

    by_id = {p["id"]: p for p in players}
    team = FPLClient().my_team(team_id)
    squad = []
    for pick in team.get("picks", []):
        p = by_id.get(pick["element"])
        if not p:
            continue
        q = dict(p)
        q["selling_price"] = int(pick.get("selling_price") or p["cost"])
        q["purchase_price"] = int(pick.get("purchase_price") or p["cost"])
        squad.append(q)

    bank = int(team.get("transfers", {}).get("bank", 0) or 0)
    tr = team.get("transfers", {})
    if tr.get("status") == "unlimited":
        ft = 5
    else:
        ft = max(1, int((tr.get("limit") or 1) - (tr.get("made") or 0)))

    planner = plan_sequences(
        squad, players, bank, ft, horizon=len(gw_ids),
        beam_width=int(settings.get("v3_beam_width", 18)),
        risk_penalty=float(settings.get("v3_risk_penalty", 0.15)))

    captain_pool = sorted(squad, key=lambda p: (p["xpts"] - 0.12 * (p["xpts_variance"] ** 0.5)), reverse=True)
    captain = captain_pool[0] if captain_pool else None
    residuals = os.path.join(BASE, "data", "processed", "residuals.csv")
    calibration = calibration_summary(residuals)

    out = {
        "model": "v3-shadow",
        "gw": gw,
        "generated_at": now.isoformat(),
        "deadline": next_gw["deadline_time"],
        "calibration": calibration,
        "captain": ({k: captain[k] for k in ("id", "name", "xpts", "xpts_floor", "xpts_upside", "p_start", "expected_minutes")}
                    if captain else None),
        "multigw_plan": planner,
        "squad": squad,
        "top_candidates": sorted(players, key=lambda p: p["xpts_horizon"], reverse=True)[:30],
    }
    path = os.path.join(BASE, "data", "processed", f"v3_shadow_gw{gw}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, path)
    print(f"v3 shadow GW{gw}: captain={captain['name'] if captain else '?'} "
          f"xPts={captain['xpts'] if captain else 0:.2f} | first={planner['first_action']} | saved={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
