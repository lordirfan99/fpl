"""Debug: inspect gw10 rows + history join for a few players."""
import os, sys
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON = os.path.join(BASE, "data/historical/vaastav/data/2025-26")
sys.path.insert(0, os.path.join(BASE, "model"))
import xpts_model as xm

gw10 = pd.read_csv(os.path.join(SEASON, "gws", "gw10.csv"))
print("gw10 shape:", gw10.shape)
print("dtypes:", dict(gw10[["element", "total_points", "minutes", "xP", "team", "position"]].dtypes))
print("element sample:", gw10["element"].dropna().unique()[:10])
print("xP non-null:", gw10["xP"].notna().sum(), "| xP == 0:", (gw10["xP"] == 0).sum(), "| xP > 0:", (gw10["xP"] > 0).sum())

frames = []
for gw in range(1, 10):
    frames.append(pd.read_csv(os.path.join(SEASON, "gws", f"gw{gw}.csv")))
prior = pd.concat(frames, ignore_index=True)

print("\n--- 6 sample players ---")
sample = gw10.sample(6, random_state=1)
for _, row in sample.iterrows():
    hist = prior[prior["element"] == row["element"]].tail(6)
    print(f"element={row['element']} {row['position']} {row['team']} | actual={row['total_points']} min={row['minutes']} xP={row.get('xP')}")
    print("  hist pts:", list(hist["total_points"]), "| mins:", list(hist["minutes"]))
    pred = xm.predict(list(hist["total_points"]), list(hist["minutes"]), fdr=3, position=row["position"])
    print("  pred:", round(pred, 2))
