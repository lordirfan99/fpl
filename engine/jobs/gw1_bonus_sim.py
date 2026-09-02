"""GW1 bonus expectations under 2026/27 BPS rules (production edge, live for GW1).

Method (leakage-safe, fixture-aware):
  1. Last-season per-90 BPS rate for every player (from vaastav 2025-26) = latent
     BPS prior, scaled by projected GW1 minutes (preseason minutes proxy from
     bootstrap, same discipline as preseason_xpts).
  2. Apply the 2026/27 CBI rule delta: BPS -= expected_CBI * (1/2 - 1/3).
  3. Group players by GW1 fixture (both teams), simulate each fixture n_sims
     times, allocate 3/2/1 with official tie rules -> per-player E[bonus],
     P(any), P(1/2/3).
  4. Write data/processed/gw1_bonus.json for the pipeline.

The row-wise logistic model stays shadow-only (failed the 5% RMSE gate on the
old-rule labels — per Sol's escalation rule it is exposed as diagnostics, NOT
promoted). This fixture simulation is the honest GW1 edge: it re-rates the
players whose bonus ceiling just changed under official coefficient deltas.

Run: .venv/Scripts/python.exe jobs/gw1_bonus_sim.py
"""
import glob
import json
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "execution"))

import bonus_model as bm

GWS_DIR = os.path.join(BASE, "data", "historical", "vaastav", "data", "2025-26", "gws")
TEAMS_CSV = os.path.join(BASE, "data", "historical", "vaastav", "data", "2025-26", "teams.csv")
OUT = os.path.join(BASE, "data", "processed", "gw1_bonus.json")
N_SIMS = 2000
SEED = 20260821


def load_season_rates():
    """Per-PLAYER-NAME: last-season per-90 BPS and per-90 CBI + minutes.

    Keyed by normalized name because FPL element IDs are NOT stable across
    seasons (2025-26 element 411 = O'Reilly, 2026-27 element 411 = Haaland).
    """
    rates = {}
    for f in sorted(glob.glob(os.path.join(GWS_DIR, "gw*.csv"))):
        df = pd.read_csv(f)
        for _, r in df.iterrows():
            nm = _norm_name(r.get("name"))
            if not nm:
                continue
            d = rates.setdefault(nm, {"name": r.get("name"), "team": r.get("team"),
                                      "minutes": 0.0, "bps": 0.0, "cbi": 0.0,
                                      "games": 0})
            mins = float(r.get("minutes", 0) or 0)
            d["minutes"] += mins
            d["bps"] += float(r.get("bps", 0) or 0)
            d["cbi"] += float(r.get("clearances_blocks_interceptions", 0) or 0)
            if mins >= 1:
                d["games"] += 1
    for nm, d in rates.items():
        m = max(d["minutes"], 1.0)
        d["bps90"] = d["bps"] * 90.0 / m
        d["cbi90"] = d["cbi"] * 90.0 / m
    return rates


def _norm_name(name):
    """Normalize a player name to ASCII lowercase compact form for matching."""
    if name is None or pd.isna(name):
        return ""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c.lower() for c in s if c.isalnum())


def main():
    print("loading season rates + fixtures...")
    rates = load_season_rates()
    bootstrap = json.load(open(os.path.join(BASE, "data", "raw", "bootstrap-static.json"), encoding="utf-8"))
    fixtures = json.load(open(os.path.join(BASE, "data", "raw", "fixtures.json"), encoding="utf-8"))
    teams_boot = {t["id"]: t["name"] for t in bootstrap["teams"]}
    # map vaastav team name -> bootstrap team id
    teams_csv = pd.read_csv(TEAMS_CSV)
    name2boot = {}
    for _, t in teams_csv.iterrows():
        # vaastav teams.csv id is the FPL team id in this dataset
        name2boot[str(t["name"]).strip()] = int(t["id"])

    gw1 = [f for f in fixtures if f.get("event") == 1 and not f.get("finished")]
    print(f"GW1 fixtures: {len(gw1)}")

    # projected minutes proxy from bootstrap (preseason): same as preseason_xpts
    def proj_minutes(el):
        status = el.get("status", "a")
        cop = el.get("chance_of_playing_next_round")
        news = (el.get("news") or "").strip()
        if status in ("i", "u") or (cop is not None and cop < 50):
            return 0.0
        minutes = float(el.get("minutes", 0) or 0)
        mp = min(0.9, 0.3 + 0.6 * (minutes / 3420.0))
        if (cop is not None and cop < 75) or news:
            mp *= 0.5
        return mp * 90.0

    out = {}
    for fx in gw1:
        players = []
        for side in ("team_h", "team_a"):
            tid = fx[side]
            squad = [p for p in bootstrap["elements"]
                     if p["team"] == tid and p.get("can_select")]
            for p in squad:
                el_id = p["id"]
                # name-keyed join: vaastav name -> season rate (IDs not stable)
                rate = rates.get(_norm_name(p["web_name"]), {})
                mins = proj_minutes(p)
                if mins <= 0:
                    continue
                bps_mean = rate.get("bps90", 0.0) * (mins / 90.0)
                cbi_expected = rate.get("cbi90", 0.0) * (mins / 90.0)
                # shrinkage: low-minute/new players pull toward position mean
                # (Sol directive: priors with shrinkage for new/low-minute players)
                games = rate.get("games", 0)
                pos_mean = {"GKP": 2.0, "DEF": 4.5, "MID": 5.5, "FWD": 7.0}.get(
                    {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}.get(p["element_type"], "MID"), 5.0)
                if games < 10:
                    k = max(1, 10 - games)
                    bps_mean = (bps_mean * games + pos_mean * (mins / 90.0) * k) / (games + k)
                players.append({
                    "id": el_id,
                    "position": bm.POSITIONS[0] if p["element_type"] == 1 else
                                ({2: "DEF", 3: "MID", 4: "FWD"}.get(p["element_type"], "MID")),
                    "bps_mean": bps_mean,
                    "cbi90_ewma": cbi_expected,  # reused by sim as expected CBI
                    "name": p["web_name"],
                    "team": tid,
                })
        # simulate BOTH teams together (official bonus is per-match across the
        # full ~22-man matchday squad, not per team)
        res = bm.simulate_fixture(players, n_sims=N_SIMS, seed=SEED,
                                  rules=bm.load_rules("2026-27"),
                                  bps_sigma=8.0)
        for pid, r in res.items():
            p = next(x for x in players if x["id"] == pid)
            out[pid] = {
                "name": p["name"], "position": p["position"],
                "e_bonus": round(r["e_bonus"], 3),
                "p_any": round(r["p_any"], 3),
                "p1": round(r["p1"], 3), "p2": round(r["p2"], 3), "p3": round(r["p3"], 3),
                "fixture": fx["id"], "team": p["team"],
                "bps_mean": round(p["bps_mean"], 2),
            }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"gw": 1, "rules": "2026-27", "n_sims": N_SIMS, "players": out},
                  f, indent=1)
    print(f"players scored: {len(out)} -> {OUT}")
    # top 15 by E[bonus]
    ranked = sorted(out.items(), key=lambda kv: -kv[1]["e_bonus"])[:15]
    print("\n=== TOP 15 GW1 E[bonus] UNDER 2026/27 RULES ===")
    print(f"{'Player':<18}{'Pos':<5}{'E[bonus]':>9}{'P(any)':>8}{'P(3)':>6}")
    for pid, r in ranked:
        print(f"{str(r['name'])[:18]:<18}{r['position']:<5}{r['e_bonus']:>9.3f}{r['p_any']:>8.3f}{r['p3']:>6.3f}")


if __name__ == "__main__":
    main()
