"""
FPL Autopilot - backtest xPts v1 vs 2025-26 actuals.

Benchmarks:
  1. our model (form + minutes prob + FDR)
  2. FPL's in-match xP (their expected points per player per game)

Metrics per GW + season: Spearman correlation, MAE, and a captain
simulation (points scored by the top predicted player, doubled).
Run: .venv/Scripts/python.exe model/backtest.py
"""
import os
import sys
import glob
import json

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON = os.path.join(BASE, "data/historical/vaastav/data/2025-26")

sys.path.insert(0, os.path.join(BASE, "model"))
import xpts_model as xm

MIN_HIST_GW = 3   # need >=3 prior games for a prediction
CAPTAIN_MIN_PROB = 0.6


def load_gws(season_dir):
    frames = []
    for path in sorted(glob.glob(os.path.join(season_dir, "gws", "gw*.csv")),
                       key=lambda p: int(os.path.basename(p)[2:-4])):
        gw = int(os.path.basename(path)[2:-4])
        df = pd.read_csv(path)
        df["gw"] = gw
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def main():
    gws = load_gws(SEASON)
    teams = pd.read_csv(os.path.join(SEASON, "teams.csv"))
    fixtures = pd.read_csv(os.path.join(SEASON, "fixtures.csv"))
    team_name_to_id = dict(zip(teams["name"], teams["id"]))

    # (gw, team_id) -> FDR
    fdr = {}
    for _, f in fixtures.iterrows():
        gw = int(f["event"])
        fdr[(gw, int(f["team_h"]))] = f["team_h_difficulty"]
        fdr[(gw, int(f["team_a"]))] = f["team_a_difficulty"]

    last_gw = int(gws["gw"].max())
    results = []
    all_rows = []

    for gw in range(MIN_HIST_GW + 1, last_gw + 1):
        prior = gws[gws["gw"] < gw]
        cur = gws[gws["gw"] == gw]
        preds, acts, xps = [], [], []
        capt_model, capt_xp, capt_opt = None, None, None
        capt_model_pts = capt_xp_pts = capt_opt_pts = 0

        for _, row in cur.iterrows():
            element = row["element"]
            hist = prior[prior["element"] == element].tail(6)
            if len(hist) < MIN_HIST_GW or hist["minutes"].sum() <= 0:
                continue
            team_id = team_name_to_id.get(row["team"])
            f = fdr.get((gw, team_id), 3)
            pred = xm.predict(list(hist["total_points"]), list(hist["minutes"]),
                              fdr=f, position=row["position"])
            preds.append(pred)
            acts.append(row["total_points"])
            xps.append(row.get("xP", 0))

            mp = xm.min_probability(list(hist["minutes"]))
            if mp >= CAPTAIN_MIN_PROB:
                if capt_model is None or pred > capt_model[0]:
                    capt_model = (pred, element)
                if capt_xp is None or row.get("xP", 0) > capt_xp[0]:
                    capt_xp = (row.get("xP", 0), element)

        # optimal captain (ceiling): 2x max actual among all players this gw
        if len(cur) > 0:
            best = cur.loc[cur["total_points"].idxmax()]
            capt_opt_pts = 2 * int(best["total_points"])

        if capt_model is not None:
            m = cur[cur["element"] == capt_model[1]]
            capt_model_pts = 2 * int(m["total_points"].iloc[0]) if len(m) else 0
        if capt_xp is not None:
            m = cur[cur["element"] == capt_xp[1]]
            capt_xp_pts = 2 * int(m["total_points"].iloc[0]) if len(m) else 0

        if len(preds) >= 10:
            s_series = pd.Series(preds)
            a_series = pd.Series(acts)
            x_series = pd.Series(xps)
            sp_model = s_series.corr(a_series, method="spearman")
            sp_xp = x_series.corr(a_series, method="spearman")
            mae_model = (a_series - s_series).abs().mean()
            mae_xp = (a_series - x_series).abs().mean()
            results.append({
                "gw": gw, "n": len(preds),
                "spearman_model": round(sp_model, 3), "spearman_xp": round(sp_xp, 3),
                "mae_model": round(mae_model, 2), "mae_xp": round(mae_xp, 2),
                "capt_model": capt_model_pts, "capt_xp": capt_xp_pts, "capt_opt": capt_opt_pts,
            })
            for p, a, x in zip(preds, acts, xps):
                all_rows.append({"gw": gw, "pred": p, "actual": a, "xP": x})

    df = pd.DataFrame(all_rows)
    out = {
        "season": "2025-26",
        "gw_range": [MIN_HIST_GW + 1, last_gw],
        "overall": {
            "n": int(len(df)),
            "spearman_model": round(df["pred"].corr(df["actual"], method="spearman"), 3),
            "spearman_xp": round(df["xP"].corr(df["actual"], method="spearman"), 3),
            "mae_model": round((df["actual"] - df["pred"]).abs().mean(), 2),
            "mae_xp": round((df["actual"] - df["xP"]).abs().mean(), 2),
        },
        "captain_totals": {
            "model": int(sum(r["capt_model"] for r in results)),
            "xP": int(sum(r["capt_xp"] for r in results)),
            "optimal": int(sum(r["capt_opt"] for r in results)),
        },
        "by_gw": results,
    }

    os.makedirs(os.path.join(BASE, "data", "processed"), exist_ok=True)
    with open(os.path.join(BASE, "data", "processed", "backtest_mvp_2025-26.json"), "w") as f:
        json.dump(out, f, indent=1)

    # ---- print report ----
    print("=" * 62)
    print("xPts v1 BACKTEST vs 2025-26 (GW%d-%d)" % (MIN_HIST_GW + 1, last_gw))
    print("=" * 62)
    o = out["overall"]
    print(f"Player-GWs scored: {o['n']}")
    print(f"Spearman (model): {o['spearman_model']}   |   Spearman (FPL xP): {o['spearman_xp']}")
    print(f"MAE (model):      {o['mae_model']}        |   MAE (FPL xP):      {o['mae_xp']}")
    print("-" * 62)
    print("CAPTAIN SIMULATION (doubled points, unconstrained pick):")
    c = out["captain_totals"]
    print(f"  Model captain: {c['model']}")
    print(f"  FPL xP captain: {c['xP']}")
    print(f"  Optimal captain (ceiling): {c['optimal']}")
    print(f"  Model vs optimal: {c['model'] / max(c['optimal'], 1) * 100:.1f}% of ceiling")
    print("-" * 62)
    print("Per-GW (gw | n | sp_model | sp_xp | mae_m | mae_x | captM | captX | captO):")
    for r in results:
        print("  %2d | %4d | %.2f | %.2f | %.1f | %.1f | %4d | %4d | %4d" % (
            r["gw"], r["n"], r["spearman_model"], r["spearman_xp"],
            r["mae_model"], r["mae_xp"], r["capt_model"], r["capt_xp"], r["capt_opt"]))
    print("=" * 62)


if __name__ == "__main__":
    main()
