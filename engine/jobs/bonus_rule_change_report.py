"""2026/27 BPS rule-change counterfactual report (Sol directive P4).

The 2025/26 bonus labels were awarded under the OLD BPS coefficients, so an ML
model trained on them cannot be "validated" against them under new rules. What
IS computable today: re-run 2025/26 fixtures under the 2026/27 CBI rule
(1 BPS per 3 actions, was 1 per 2) and quantify the bonus shift per player.

Output: reports/bonus_rule_change_2026_27.json + printed table sorted by bonus
points gained/lost. This is the honest GW1 edge: it re-rates the players whose
bonus ceiling just changed, without pretending to predict bonus from scratch.

Run: .venv/Scripts/python.exe jobs/bonus_rule_change_report.py
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))


DATA = os.path.join(BASE, "data", "historical", "vaastav", "data", "2025-26")
GWS_DIR = os.path.join(DATA, "gws")
TEAMS_CSV = os.path.join(DATA, "teams.csv")


def load_all_gws():
    frames = []
    for f in sorted(glob.glob(os.path.join(GWS_DIR, "gw*.csv"))):
        frames.append(pd.read_csv(f))
    return pd.concat(frames, ignore_index=True)


def main():
    df = load_all_gws()
    teams = pd.read_csv(TEAMS_CSV)
    team_name = {int(t["id"]): str(t["name"]) for _, t in teams.iterrows()}

    # Per player: season CBI total, minutes, actual bonus under old rules.
    # Group by element id (player).
    agg = {}
    for _, r in df.iterrows():
        el = r.get("element")
        if el is None or pd.isna(el):
            continue
        el = int(el)
        d = agg.setdefault(el, {
            "name": r.get("name"), "team": r.get("team"),
            "minutes": 0.0, "cbi": 0.0, "bonus_old": 0.0,
            "bps_old": 0.0, "bonus_gws": 0,
        })
        d["minutes"] += float(r.get("minutes", 0) or 0)
        d["cbi"] += float(r.get("clearances_blocks_interceptions", 0) or 0)
        d["bonus_old"] += float(r.get("bonus", 0) or 0)
        d["bps_old"] += float(r.get("bps", 0) or 0)
        if float(r.get("bonus", 0) or 0) >= 1:
            d["bonus_gws"] += 1

    # CBI rule delta: BPS loss = CBI * (1/2 - 1/3) = CBI / 6
    rows = []
    for el, d in agg.items():
        bps_loss = d["cbi"] / 6.0
        d["bps_new"] = max(0.0, d["bps_old"] - bps_loss)
        d["cbi_bps_loss"] = bps_loss
        rows.append(d)

    out_df = pd.DataFrame(rows)
    out_df = out_df.sort_values("cbi_bps_loss", ascending=False)

    print("=== TOP 30 BPS LOSERS UNDER 2026/27 CBI RULE (CBI: 1-per-3 vs 1-per-2) ===")
    print(f"{'Player':<22}{'Team':<16}{'CBI':>6}{'BPS lost':>10}{'Bonus GWs 25/26':>16}")
    for _, r in out_df.head(30).iterrows():
        print(f"{str(r['name'])[:22]:<22}{str(r['team'])[:16]:<16}{r['cbi']:>6.0f}{r['cbi_bps_loss']:>10.1f}{r['bonus_gws']:>16.0f}")

    # Also show players who GAIN relative standing (low CBI, high attacking output)
    out_df["bonus_per_min"] = out_df["bonus_old"] / out_df["minutes"].replace(0, np.nan)
    attackers = out_df[out_df["minutes"] >= 1500].sort_values("bonus_per_min", ascending=False)
    print("\n=== HIGHEST BONUS RATE (>=1500 min) — UNTOUCHED BY CBI RULE ===")
    print(f"{'Player':<22}{'Team':<16}{'Bonus/90':>10}{'Bonus total':>12}")
    for _, r in attackers.head(15).iterrows():
        print(f"{str(r['name'])[:22]:<22}{str(r['team'])[:16]:<16}{r['bonus_per_min']*90:>10.2f}{r['bonus_old']:>12.0f}")

    os.makedirs(os.path.join(BASE, "reports"), exist_ok=True)
    out_path = os.path.join(BASE, "reports", "bonus_rule_change_2026_27.json")
    out_df.head(200).to_json(out_path, orient="records", indent=1)
    print(f"\nsaved -> {out_path}")


if __name__ == "__main__":
    main()
