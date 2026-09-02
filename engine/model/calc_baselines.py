"""Compute per-position per-90 baseline from a completed season (no lookahead).
Writes model/position_baselines.json"""
import os, json
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON = os.path.join(BASE, "data/historical/vaastav/data/2024-25")

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
print("2024-25 rows:", len(gws), "| GWs:", gws["gw"].min(), "-", gws["gw"].max())

# per-90 points, filtered to >= 20 min, clipped [0,25]
sub = gws[gws["minutes"] >= 20].copy()
sub["per90"] = (sub["total_points"] * 90.0 / sub["minutes"]).clip(0, 25)

baselines = {}
print("\nPosition baselines (2024-25, per-90):")
for pos, grp in sub.groupby("position"):
    b = float(grp["per90"].mean())
    baselines[pos] = round(b, 3)
    print(f"  {pos}: {b:.3f}  (n={len(grp)})")

with open(os.path.join(BASE, "model", "position_baselines.json"), "w") as f:
    json.dump(baselines, f, indent=2)
print("\nSaved model/position_baselines.json")
