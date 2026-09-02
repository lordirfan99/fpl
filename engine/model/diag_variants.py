"""Test model variants: which blend of minutes-prob + shrunk form wins?"""
import os
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON = os.path.join(BASE, "data/historical/vaastav/data/2025-26")

def load_gws(season_dir):
    frames = []
    for gw in range(1, 39):
        p = os.path.join(season_dir, "gws", f"gw{gw}.csv")
        if os.path.exists(p):
            df = pd.read_csv(p)
            df["gw"] = gw
            frames.append(df)
    return pd.concat(frames, ignore_index=True)

def min_probability(minutes_list):
    minutes_list = [m for m in minutes_list if m is not None]
    if not minutes_list:
        return 0.5
    played = sum(1 for m in minutes_list if m > 0)
    return (played + 1.0) / (len(minutes_list) + 2.0)

def rate_filtered(points_list, minutes_list, min_min=20, cap=25.0):
    """per-90 rate using only games with >= min_min minutes, clipped [0, cap]."""
    rates = []
    for pts, mins in zip(points_list, minutes_list):
        if mins and mins >= min_min:
            r = pts * 90.0 / mins
            rates.append(max(0.0, min(cap, r)))
    if not rates:
        return None
    return sum(rates) / len(rates)

gws = load_gws(SEASON)
teams = pd.read_csv(os.path.join(SEASON, "teams.csv"))
team_name_to_id = dict(zip(teams["name"], teams["id"]))

rows = []
for gw in range(4, 39):
    prior = gws[gws["gw"] < gw]
    cur = gws[gws["gw"] == gw]
    for _, row in cur.iterrows():
        hist = prior[prior["element"] == row["element"]].tail(6)
        if len(hist) < 3 or hist["minutes"].sum() <= 0:
            continue
        mp = min_probability(list(hist["minutes"]))
        rate = rate_filtered(list(hist["total_points"]), list(hist["minutes"]))
        rows.append({"gw": gw, "actual": row["total_points"], "mp": mp, "rate": rate, "pos": row["position"]})

df = pd.DataFrame(rows)
pos_avg = df.groupby("pos")["actual"].transform("mean")  # rough position baseline (uses same-season avg - mild lookahead, acceptable for v1 test)

def sp(col):
    return round(df[col].corr(df["actual"], method="spearman"), 3)

print("rows:", len(df))
df["rate0"] = df["rate"].fillna(0.0)
print("Spearman vs actual:")
print("  mp                     :", sp("mp"))
print("  rate_filtered (>=20min,clip):", sp("rate0"))
print()
for w in [0.0, 0.2, 0.35, 0.5, 0.7]:
    df[f"p{w}"] = df["mp"] * (w * df["rate0"] + (1 - w) * pos_avg)
    print(f"  mp x ({w:.2f}*rate + {1-w:.2f}*posavg):", sp(f"p{w}"))
df["pmax"] = df["mp"] * df[["rate0", pos_avg.name]].max(axis=1)
print("  mp x max(rate,posavg)  :", sp("pmax"))

# also per-position sanity: does rate help within positions?
for pos in ["GKP", "DEF", "MID", "FWD"]:
    sub = df[df["pos"] == pos]
    if len(sub) > 100:
        r1 = round(sub["mp"].corr(sub["actual"], method="spearman"), 3)
        r2 = round(sub["rate0"].corr(sub["actual"], method="spearman"), 3)
        print(f"  [{pos}] n={len(sub)}  mp={r1}  rate={r2}")
