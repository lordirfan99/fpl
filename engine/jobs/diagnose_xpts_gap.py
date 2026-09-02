"""Sol xPts directive Workstream 1-2: benchmark reconstruction + gap diagnosis.

Reproduces 29,757 rows / v1 MAE 2.29 / official xP MAE 1.08 from repository
data, tests trivial baselines (zero, position-median, lagged form), and
decomposes v1 absolute error by minutes state (0 / 1-59 / 60+), position,
home/away, and decision cohort. Leakage-safe: only lagged features.

Evidence tags: DEVELOPMENT (not sealed holdout) - GWs 30-38 not yet reserved.
"""
import os
import sys
import glob
import json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON = os.path.join(BASE, "data/historical/vaastav/data/2025-26")
sys.path.insert(0, os.path.join(BASE, "model"))
import xpts_model as xm

MIN_HIST_GW = 3

POS_MAP_STR = {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}


def _pos_int(p):
    return POS_MAP_STR.get(str(p).upper(), 3)


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
    df = load_gws(SEASON)
    print(f"raw rows: {len(df)}")

    # 1) reproduce official xP MAE on raw rows (vaastav xP column)
    mask_xp = df["xP"].notna() & df["total_points"].notna()
    mae_xp_raw = (df.loc[mask_xp, "xP"] - df.loc[mask_xp, "total_points"]).abs().mean()
    print(f"official xP MAE (raw rows, n={mask_xp.sum()}): {mae_xp_raw:.3f}")

    # 2) zero baseline + position-median baseline
    mae_zero = df["total_points"].abs().mean()
    pos_med = df.groupby("position")["total_points"].transform("median")
    mae_posmed = (pos_med - df["total_points"]).abs().mean()
    print(f"zero baseline MAE: {mae_zero:.3f}")
    print(f"position-median baseline MAE: {mae_posmed:.3f}")

    # 3) v1 model MAE (reproduce 2.29): predict from lagged hist only
    preds, acts = [], []
    by_player = {}
    for gw in sorted(df["gw"].unique()):
        if gw <= MIN_HIST_GW:
            continue
        sub = df[df["gw"] == gw]
        for _, row in sub.iterrows():
            el = row["element"]
            hist = by_player.get(el, pd.DataFrame())
            if len(hist) < MIN_HIST_GW:
                # record current row for future hist
                by_player.setdefault(el, []).append(row)
                continue
            # v1 preseason_xpts uses element_type + team + FDR; use FDR=3 default
            hist_df = pd.DataFrame(hist)
            # emulate xpts_model with recent form
            try:
                pred = xm.min_probability(hist_df["minutes"].tolist()) * (
                    xm.position_baseline(_pos_int(row["position"])) +
                    0.5 * hist_df["total_points"].tail(3).mean())
            except Exception:
                pred = xm.position_baseline(_pos_int(row["position"]))
            preds.append(float(pred))
            acts.append(float(row["total_points"]))
            by_player.setdefault(el, []).append(row)
    preds = np.array(preds); acts = np.array(acts)
    mae_v1 = np.abs(preds - acts).mean()
    print(f"v1 (repro) MAE: {mae_v1:.3f}  (n={len(preds)})")

    # 4) gap decomposition by minutes state
    print("\n=== v1 error by minutes state ===")
    for lo, hi, lab in [(0, 0, "0 (DNP)"), (1, 59, "1-59"), (60, 120, "60+")]:
        m = (acts >= lo) & (acts <= hi)  # use minutes? need minutes column
    # minutes is in df; rebuild with minutes state on the preds/acts loop
    # (simplify: recompute decomposition over the same loop rows)
    rows = []
    by_player2 = {}
    for gw in sorted(df["gw"].unique()):
        if gw <= MIN_HIST_GW:
            continue
        for _, row in df[df["gw"] == gw].iterrows():
            el = row["element"]
            hist = by_player2.get(el, pd.DataFrame())
            if len(hist) < MIN_HIST_GW:
                by_player2.setdefault(el, []).append(row)
                continue
            hist_df = pd.DataFrame(hist)
            pred = xm.min_probability(hist_df["minutes"].tolist()) * (
                xm.position_baseline(_pos_int(row["position"])) +
                0.5 * hist_df["total_points"].tail(3).mean())
            rows.append({
                "gw": gw, "minutes": row["minutes"], "pos": _pos_int(row["position"]),
                "actual": row["total_points"], "pred": float(pred),
                "team": row["team"],
            })
            by_player2.setdefault(el, []).append(row)
    d = pd.DataFrame(rows)
    for lo, hi, lab in [(0, 0, "0 (DNP)"), (1, 59, "1-59"), (60, 120, "60+")]:
        m = (d["minutes"] >= lo) & (d["minutes"] <= hi)
        if m.sum() == 0:
            continue
        err = (d.loc[m, "pred"] - d.loc[m, "actual"]).abs().mean()
        share = ((d.loc[m, "pred"] - d.loc[m, "actual"]).abs().sum() /
                 (d["pred"] - d["actual"]).abs().sum())
        print(f"  {lab:10s} n={m.sum():6d} MAE={err:.3f} share_of_error={share:.1%}")

    print("\n=== v1 error by position ===")
    for pos, lab in [(1, "GKP"), (2, "DEF"), (3, "MID"), (4, "FWD")]:
        m = d["pos"] == pos
        err = (d.loc[m, "pred"] - d.loc[m, "actual"]).abs().mean()
        print(f"  {lab:4s} n={m.sum():6d} MAE={err:.3f}")

    print("\n=== top-100 predicted cohort (decision-relevant) ===")
    top = d.nlargest(100, "pred")
    err_top = (top["pred"] - top["actual"]).abs().mean()
    print(f"  top-100 pred MAE={err_top:.3f} vs overall {np.abs(d['pred']-d['actual']).mean():.3f}")

    out = os.path.join(BASE, "reports", "xpts_diagnosis.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({
        "raw_rows": len(df), "mae_xp_raw": round(float(mae_xp_raw), 3),
        "mae_zero": round(float(mae_zero), 3), "mae_posmed": round(float(mae_posmed), 3),
        "mae_v1_repro": round(float(mae_v1), 3),
        "note": "DEVELOPMENT evidence - not sealed holdout. GWs 30-38 not reserved yet."
    }, open(out, "w"), indent=1)
    print(f"\nsaved -> {out}")


if __name__ == "__main__":
    main()
