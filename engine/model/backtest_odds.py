"""
FPL Autopilot - backtest v2: odds-blended xPts vs v1 (FDR-only) on 2025-26.

v1 (existing): xPts = play_prob x max(form_per90, baseline) x fdr_multiplier(FDR)
v2 (NEW):      xPts = play_prob x max(form_per90, baseline) x odds_multiplier(odds)

odds_multiplier converts match odds (1X2, de-vigged) into a team-strength
multiplier per player position:
  - MID/FWD: scales with implied win probability of the player's team
             (stronger team -> more attacking output)
  - GKP/DEF: scales with implied clean-sheet likelihood, proxied by the
             opponent's weakness (1 - opponent win prob)
Odds come from football-data.co.uk (Pinnacle PSH/PSD/PSA primary, Bet365 fallback).

Backtest window: SAME as v1 (GW4-38) so comparisons are honest.
Metrics: Spearman, MAE, captain simulation - model v1 vs model v2 vs FPL xP.
Run: .venv/Scripts/python.exe model/backtest_odds.py
"""
import os
import sys
import glob
import json

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON = os.path.join(BASE, "data/historical/vaastav/data/2025-26")
ODDS_CSV = os.path.join(BASE, "data/historical/odds/E0_2025-26.csv")

sys.path.insert(0, os.path.join(BASE, "model"))
import xpts_model as xm

MIN_HIST_GW = 3
CAPTAIN_MIN_PROB = 0.6

# football-data.co.uk -> vaastav team name
TEAM_ALIAS = {
    "Man Utd": "Man United",
    "Spurs": "Tottenham",
    "Nott'm Forest": "Nott'm Forest",
}


def devig(h, d, a):
    """Convert decimal 1X2 odds to fair probabilities (proportional de-vig)."""
    ih, id_, ia = 1.0 / h, 1.0 / d, 1.0 / a
    s = ih + id_ + ia
    return ih / s, id_ / s, ia / s


def load_odds():
    df = pd.read_csv(ODDS_CSV)
    # Pinnacle preferred; fall back to Bet365 where Pinnacle missing
    odds = {}
    for _, r in df.iterrows():
        ph, pd_, pa = r.get("PSH"), r.get("PSD"), r.get("PSA")
        if pd.isna(ph) or pd.isna(pd_) or pd.isna(pa):
            ph, pd_, pa = r.get("B365H"), r.get("B365D"), r.get("B365A")
        if pd.isna(ph) or pd.isna(pd_) or pd.isna(pa):
            continue
        try:
            home = TEAM_ALIAS.get(r["HomeTeam"], r["HomeTeam"])
            away = TEAM_ALIAS.get(r["AwayTeam"], r["AwayTeam"])
        except KeyError:
            continue
        odds[(home, away)] = devig(float(ph), float(pd_), float(pa))
    return odds


def odds_multiplier(team_win_prob, opp_win_prob, position):
    """Team-strength multiplier from de-vigged odds, per position."""
    if position in ("GKP", "GK", "DEF"):
        # clean-sheet likelihood: opponent weakness drives it
        return min(1.25, max(0.75, 0.9 + 0.5 * (1.0 - opp_win_prob - 0.35)))
    # MID/FWD: attacking output scales with implied win probability
    return min(1.25, max(0.75, 0.85 + 0.8 * (team_win_prob - 0.40)))


def main():
    gws = pd.concat(
        [pd.read_csv(p).assign(gw=int(os.path.basename(p)[2:-4]))
         for p in sorted(glob.glob(os.path.join(SEASON, "gws", "gw*.csv")),
                         key=lambda p: int(os.path.basename(p)[2:-4]))],
        ignore_index=True)
    teams = pd.read_csv(os.path.join(SEASON, "teams.csv"))
    fixtures = pd.read_csv(os.path.join(SEASON, "fixtures.csv"))
    team_name_to_id = dict(zip(teams["name"], teams["id"]))
    id_to_name = {v: k for k, v in team_name_to_id.items()}

    fdr = {}
    for _, f in fixtures.iterrows():
        gw = int(f["event"])
        fdr[(gw, int(f["team_h"]))] = f["team_h_difficulty"]
        fdr[(gw, int(f["team_a"]))] = f["team_a_difficulty"]

    odds = load_odds()
    print(f"odds loaded: {len(odds)} unique (home,away) pairs")

    last_gw = int(gws["gw"].max())
    results = []
    all_rows = []

    for gw in range(MIN_HIST_GW + 1, last_gw + 1):
        prior = gws[gws["gw"] < gw]
        cur = gws[gws["gw"] == gw]
        preds_v1, preds_v2, acts, xps = [], [], [], []
        caps = {"v1": None, "v2": None, "xp": None}
        capt_pts = {"v1": 0, "v2": 0, "xp": 0, "opt": 0}

        for _, row in cur.iterrows():
            element = row["element"]
            hist = prior[prior["element"] == element].tail(6)
            if len(hist) < MIN_HIST_GW or hist["minutes"].sum() <= 0:
                continue
            team_id = team_name_to_id.get(row["team"])
            if team_id is None:
                continue
            team_name = id_to_name.get(team_id)
            f = fdr.get((gw, team_id), 3)
            pred_v1 = xm.predict(list(hist["total_points"]), list(hist["minutes"]),
                                  fdr=f, position=row["position"])

            # v2: find this player's team fixture in odds by (gw, team)
            # odds keyed by (home,away); find the pair containing team_name for this gw
            m_win = None
            opp_win = None
            # simplest: look up via fixtures csv for this gw
            for _, fx in fixtures.iterrows():
                if int(fx["event"]) != gw:
                    continue
                h_name = id_to_name.get(int(fx["team_h"]))
                a_name = id_to_name.get(int(fx["team_a"]))
                pair = odds.get((h_name, a_name))
                if pair is None:
                    pair = odds.get((a_name, h_name))  # fallback reverse
                if pair is None:
                    continue
                if team_name == h_name:
                    m_win, opp_win = pair[0], pair[1]
                elif team_name == a_name:
                    m_win, opp_win = pair[1], pair[0]
                if m_win is not None:
                    break
            if m_win is None:
                pred_v2 = pred_v1  # no odds -> fall back to v1 (keeps comparison fair)
            else:
                om = odds_multiplier(m_win, opp_win, row["position"])
                pred_v2 = xm.min_probability(list(hist["minutes"])) * \
                    max(xm.per90_rate(list(hist["total_points"]), list(hist["minutes"])) or 0,
                        xm.position_baseline(row["position"])) * om

            preds_v1.append(pred_v1)
            preds_v2.append(pred_v2)
            acts.append(row["total_points"])
            xps.append(row.get("xP", 0))

            mp = xm.min_probability(list(hist["minutes"]))
            if mp >= CAPTAIN_MIN_PROB:
                if caps["v1"] is None or pred_v1 > caps["v1"][0]:
                    caps["v1"] = (pred_v1, element)
                if caps["v2"] is None or pred_v2 > caps["v2"][0]:
                    caps["v2"] = (pred_v2, element)
                if caps["xp"] is None or row.get("xP", 0) > caps["xp"][0]:
                    caps["xp"] = (row.get("xP", 0), element)

        if len(cur) > 0:
            best = cur.loc[cur["total_points"].idxmax()]
            capt_pts["opt"] = 2 * int(best["total_points"])
        for key in ("v1", "v2", "xp"):
            if caps[key] is not None:
                m = cur[cur["element"] == caps[key][1]]
                capt_pts[key] = 2 * int(m["total_points"].iloc[0]) if len(m) else 0

        if len(preds_v1) >= 10:
            s_v1 = pd.Series(preds_v1).corr(pd.Series(acts), method="spearman")
            s_v2 = pd.Series(preds_v2).corr(pd.Series(acts), method="spearman")
            s_xp = pd.Series(xps).corr(pd.Series(acts), method="spearman")
            results.append({
                "gw": gw, "n": len(preds_v1),
                "spearman_v1": round(s_v1, 3), "spearman_v2": round(s_v2, 3), "spearman_xp": round(s_xp, 3),
                "mae_v1": round((pd.Series(acts) - pd.Series(preds_v1)).abs().mean(), 2),
                "mae_v2": round((pd.Series(acts) - pd.Series(preds_v2)).abs().mean(), 2),
                "mae_xp": round((pd.Series(acts) - pd.Series(xps)).abs().mean(), 2),
                "capt_v1": capt_pts["v1"], "capt_v2": capt_pts["v2"],
                "capt_xp": capt_pts["xp"], "capt_opt": capt_pts["opt"],
            })
            for p1, p2, a, x in zip(preds_v1, preds_v2, acts, xps):
                all_rows.append({"gw": gw, "pred_v1": p1, "pred_v2": p2, "actual": a, "xP": x})

    df = pd.DataFrame(all_rows)
    out = {
        "season": "2025-26",
        "gw_range": [MIN_HIST_GW + 1, last_gw],
        "overall": {
            "n": int(len(df)),
            "spearman_v1": round(df["pred_v1"].corr(df["actual"], method="spearman"), 3),
            "spearman_v2": round(df["pred_v2"].corr(df["actual"], method="spearman"), 3),
            "spearman_xp": round(df["xP"].corr(df["actual"], method="spearman"), 3),
            "mae_v1": round((df["actual"] - df["pred_v1"]).abs().mean(), 2),
            "mae_v2": round((df["actual"] - df["pred_v2"]).abs().mean(), 2),
            "mae_xp": round((df["actual"] - df["xP"]).abs().mean(), 2),
        },
        "captain_totals": {
            "v1": int(sum(r["capt_v1"] for r in results)),
            "v2": int(sum(r["capt_v2"] for r in results)),
            "xp": int(sum(r["capt_xp"] for r in results)),
            "optimal": int(sum(r["capt_opt"] for r in results)),
        },
        "by_gw": results,
    }

    os.makedirs(os.path.join(BASE, "data", "processed"), exist_ok=True)
    with open(os.path.join(BASE, "data", "processed", "backtest_odds_2025-26.json"), "w") as f:
        json.dump(out, f, indent=1)

    # ---- report ----
    print("=" * 70)
    print("xPts v1 (FDR) vs v2 (ODDS) BACKTEST vs 2025-26 (GW%d-%d)" % (MIN_HIST_GW + 1, last_gw))
    print("=" * 70)
    o = out["overall"]
    print(f"Player-GWs scored: {o['n']}")
    print(f"Spearman | v1(FDR): {o['spearman_v1']} | v2(ODDS): {o['spearman_v2']} | FPL xP: {o['spearman_xp']}")
    print(f"MAE      | v1(FDR): {o['mae_v1']} | v2(ODDS): {o['mae_v2']} | FPL xP: {o['mae_xp']}")
    print("-" * 70)
    print("CAPTAIN SIMULATION (doubled points):")
    c = out["captain_totals"]
    print(f"  v1(FDR): {c['v1']} | v2(ODDS): {c['v2']} | FPL xP: {c['xp']} | Optimal: {c['optimal']}")
    print(f"  v1 % of ceiling: {c['v1'] / max(c['optimal'], 1) * 100:.1f}%")
    print(f"  v2 % of ceiling: {c['v2'] / max(c['optimal'], 1) * 100:.1f}%")
    print(f"  v2 - v1 captain delta: {c['v2'] - c['v1']:+d} pts over season")
    print("-" * 70)
    wins = sum(1 for r in results if r["spearman_v2"] > r["spearman_v1"])
    print(f"v2 beats v1 Spearman in {wins}/{len(results)} GWs")
    mae_wins = sum(1 for r in results if r["mae_v2"] < r["mae_v1"])
    print(f"v2 beats v1 MAE in {mae_wins}/{len(results)} GWs")
    print("=" * 70)


if __name__ == "__main__":
    main()
