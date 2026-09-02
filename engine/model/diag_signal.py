"""Diagnose which feature carries predictive signal (2025-26)."""
import os, sys
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON = os.path.join(BASE, "data/historical/vaastav/data/2025-26")
sys.path.insert(0, os.path.join(BASE, "model"))
import xpts_model as xm

def load_gws(season_dir):
    frames = []
    for gw in range(1, 39):
        p = os.path.join(season_dir, "gws", f"gw{gw}.csv")
        if os.path.exists(p):
            df = pd.read_csv(p)
            df["gw"] = gw
            frames.append(df)
    return pd.concat(frames, ignore_index=True)

gws = load_gws(SEASON)
teams = pd.read_csv(os.path.join(SEASON, "teams.csv"))
fixtures = pd.read_csv(os.path.join(SEASON, "fixtures.csv"))
team_name_to_id = dict(zip(teams["name"], teams["id"]))
fdr = {}
for _, f in fixtures.iterrows():
    fdr[(int(f["event"]), int(f["team_h"]))] = f["team_h_difficulty"]
    fdr[(int(f["event"]), int(f["team_a"]))] = f["team_a_difficulty"]

rows = []
for gw in range(4, 39):
    prior = gws[gws["gw"] < gw]
    cur = gws[gws["gw"] == gw]
    for _, row in cur.iterrows():
        hist = prior[prior["element"] == row["element"]].tail(6)
        if len(hist) < 3 or hist["minutes"].sum() <= 0:
            continue
        mp = xm.min_probability(list(hist["minutes"]))
        rate = xm.per90_rate(list(hist["total_points"]), list(hist["minutes"]))
        f = fdr.get((gw, team_name_to_id.get(row["team"])), 3)
        pred = rate * mp * xm.fdr_multiplier(f, row["position"])
        rows.append({"gw": gw, "actual": row["total_points"], "rate": rate, "mp": mp, "pred": pred,
                     "min_last6": int(hist["minutes"].sum())})

df = pd.DataFrame(rows)
print("rows:", len(df))
print("actual describe:", df["actual"].describe().round(2).to_dict())
print("pred describe:", df["pred"].describe().round(2).to_dict())
print()
print("Spearman vs actual:")
print("  rate alone :", round(df["rate"].corr(df["actual"], method="spearman"), 3))
print("  mp alone   :", round(df["mp"].corr(df["actual"], method="spearman"), 3))
print("  pred (fixed):", round(df["pred"].corr(df["actual"], method="spearman"), 3))
print()
print("Players with 0 actual pts (share):", round((df["actual"] == 0).mean(), 3))
print("Players with 0 actual pts but mins>0:", round(((df["actual"] == 0) & (df["min_last6"] > 0)).mean(), 3))
