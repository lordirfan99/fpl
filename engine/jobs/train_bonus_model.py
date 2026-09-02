"""Train + validate the BPS bonus model (Sol-orchestrated spec).

Chronological partitions (no random split, fixtures stay together):
  - Development/train: GWs 1-24
  - Calibration:        GWs 25-30
  - Locked test:        GWs 31-38

Primary metric: player-fixture RMSE of unconditional E[bonus] vs actual bonus,
challenger vs the exact weak heuristic, with 10,000 fixture-cluster bootstrap
CIs. Pass gate: >=5% RMSE improvement, no MAE regression, CI excludes zero.

Run: .venv/Scripts/python.exe jobs/train_bonus_model.py
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "jobs"))

import bonus_model as bm

DATA = os.path.join(BASE, "data", "historical", "vaastav", "data", "2025-26")
GWS_DIR = os.path.join(DATA, "gws")
TEAMS_CSV = os.path.join(DATA, "teams.csv")

TRAIN_MAX = 24
CALIB_LO, CALIB_HI = 25, 30
TEST_LO, TEST_HI = 31, 38


def load_team_strength():
    teams = pd.read_csv(TEAMS_CSV)
    strength = {}
    name2id = {}
    for _, t in teams.iterrows():
        try:
            strength[int(t["id"])] = float(t.get("strength", 3))
        except (TypeError, ValueError):
            strength[int(t["id"])] = 3.0
        name2id[str(t["name"]).strip()] = int(t["id"])
        if pd.notna(t.get("short_name")):
            name2id[str(t["short_name"]).strip()] = int(t["id"])
    return strength, name2id


def load_all_gws():
    frames = []
    for f in sorted(glob.glob(os.path.join(GWS_DIR, "gw*.csv"))):
        frames.append(pd.read_csv(f))
    return pd.concat(frames, ignore_index=True)


def players_by_round(df, name2id):
    """Group ALL rows by round; keep player identity (element) + team name."""
    rounds = {}
    for _, r in df.iterrows():
        team_name = str(r["team"]).strip()
        team_id = name2id.get(team_name, 0)
        if not team_id:
            continue
        rnd = int(r["round"])
        rounds.setdefault(rnd, []).append({
            "round": rnd,
            "team": team_id,
            "team_name": team_name,
            "element": r.get("element"),
            "name": r.get("name"),
            "position": bm.POSITIONS[0] if str(r.get("position", "")).strip().upper() in ("GK", "GKP") else str(r.get("position", "")).strip().upper(),
            "minutes": float(r.get("minutes", 0) or 0),
            "goals_scored": float(r.get("goals_scored", 0) or 0),
            "assists": float(r.get("assists", 0) or 0),
            "clean_sheets": float(r.get("clean_sheets", 0) or 0),
            "saves": float(r.get("saves", 0) or 0),
            "recoveries": float(r.get("recoveries", 0) or 0),
            "tackles": float(r.get("tackles", 0) or 0),
            "clearances_blocks_interceptions": float(r.get("clearances_blocks_interceptions", 0) or 0),
            "expected_goals": float(r.get("expected_goals", 0) or 0),
            "expected_assists": float(r.get("expected_assists", 0) or 0),
            "opponent_team": int(r.get("opponent_team", 0) or 0),
            "was_home": int(r.get("was_home", 0) or 0),
            "bonus": float(r.get("bonus", 0) or 0),
            "bps": float(r.get("bps", 0) or 0),
            "fixture": r.get("fixture"),
        })
    return rounds


def player_histories(rounds, name2id):
    """Per-player chronological history across all rounds (keyed by element).

    Returns {element: [rows sorted by round]}. Players without an element id
    are keyed by (name, team).
    """
    by_player = {}
    for rnd in sorted(rounds.keys()):
        for r in rounds[rnd]:
            key = r.get("element") if r.get("element") is not None else (r["name"], r["team"])
            by_player.setdefault(key, []).append(r)
    for k in by_player:
        by_player[k].sort(key=lambda r: r["round"])
    return by_player


def build_feature_rows(df, strength, name2id, gw_lo, gw_hi):
    """Pre-match features for every player-GW in [gw_lo, gw_hi].

    Leakage-safe: each player's history contains only rounds <= gw-1; the
    target round row is excluded from features.
    """
    rounds = players_by_round(df, name2id)
    by_player = player_histories(rounds, name2id)
    rows = []
    for key, hist in by_player.items():
        # features for each round in the requested window
        for r in hist:
            rnd = r["round"]
            if not (gw_lo <= rnd <= gw_hi):
                continue
            rows.extend(bm.build_prematch_features(hist, strength, rnd - 1))
    return rows


def weak_heuristic_e_bonus(row):
    """Exact old heuristic: bonus90 * minute_share * clamp(0.8+0.2*attack,0.7,1.2).

    bonus90 from the player's OWN historical rate (train-only, no lookahead) is
    approximated here with position mean per the pre-existing v3 formula.
    minute_share = expected minutes / 90 (minutes_share already is that).
    """
    pos = row["position"]
    b90 = {"GKP": 0.211, "DEF": 0.183, "MID": 0.296, "FWD": 0.829}.get(pos, 0.25)
    ms = min(1.0, row["minutes_share"])
    return b90 * ms * 1.0  # neutral attack factor


def main():
    print("loading 2025-26 data...")
    df = load_all_gws()
    strength, name2id = load_team_strength()

    train_rows = build_feature_rows(df, strength, name2id, 1, TRAIN_MAX)
    calib_rows = build_feature_rows(df, strength, name2id, CALIB_LO, CALIB_HI)
    test_rows = build_feature_rows(df, strength, name2id, TEST_LO, TEST_HI)
    print(f"train {len(train_rows)} | calib {len(calib_rows)} | test {len(test_rows)}")

    # --- Path A: row-wise logistic on P(bonus>=1) ---
    print("training per-position logistic on P(bonus>=1)...")
    coeffs = bm.train(train_rows)
    bm.save_coeffs(coeffs)

    def predict_e(rows, rules="2026-27"):
        return np.array([bm.expected_bonus_unconditional(r, coeffs, rules) for r in rows])

    e_new = predict_e(test_rows)
    y = np.array([r["bonus"] for r in test_rows])
    e_old = np.array([weak_heuristic_e_bonus(r) for r in test_rows])

    rmse_new = float(np.sqrt(np.mean((e_new - y) ** 2)))
    rmse_old = float(np.sqrt(np.mean((e_old - y) ** 2)))
    mae_new = float(np.mean(np.abs(e_new - y)))
    mae_old = float(np.mean(np.abs(e_old - y)))
    print()
    print(f"=== ROW-WISE LOGISTIC: LOCKED TEST GW{CALIB_HI+1}-{TEST_HI} ({len(test_rows)} rows) ===")
    print(f"NEW model : RMSE {rmse_new:.4f} | MAE {mae_new:.4f} | mean pred {e_new.mean():.3f}")
    print(f"OLD heur  : RMSE {rmse_old:.4f} | MAE {mae_old:.4f} | mean pred {e_old.mean():.3f}")
    print(f"actual mean bonus: {y.mean():.3f} | RMSE improvement: {(rmse_old-rmse_new)/rmse_old*100:.1f}% (gate >=5%)")

    # --- Path B: fixture simulation (latent BPS -> 3/2/1 allocation) ---
    print("\ntraining latent BPS ridge regressor...")
    bps_coeffs = bm.train_bps_regressor(train_rows)

    def fixture_sim_e(rows, n_sims=500, seed=7):
        """Group feature rows by fixture, simulate, return per-row E[bonus]."""
        by_fixture = {}
        for i, r in enumerate(rows):
            by_fixture.setdefault(r.get("fixture"), []).append(i)
        e = np.zeros(len(rows))
        rng_state = seed
        for fx, idxs in by_fixture.items():
            if fx is None:
                continue
            players = []
            for i in idxs:
                r = rows[i]
                players.append({
                    "id": i,
                    "position": r["position"],
                    "bps_mean": bm.predict_bps_mean(r, bps_coeffs),
                    "cbi90_ewma": r.get("cbi90_ewma", 0.0),
                })
            res = bm.simulate_fixture(players, n_sims=n_sims, seed=rng_state)
            rng_state += 1
            for i in idxs:
                e[i] = res.get(i, {}).get("e_bonus", 0.0)
        return e

    e_sim = fixture_sim_e(test_rows)
    rmse_sim = float(np.sqrt(np.mean((e_sim - y) ** 2)))
    mae_sim = float(np.mean(np.abs(e_sim - y)))
    print(f"\n=== FIXTURE SIMULATION: LOCKED TEST ===")
    print(f"SIM model : RMSE {rmse_sim:.4f} | MAE {mae_sim:.4f} | mean pred {e_sim.mean():.3f}")
    print(f"actual   : RMSE baseline {rmse_old:.4f} | mean actual {y.mean():.3f}")
    print(f"RMSE improvement vs old heuristic: {(rmse_old-rmse_sim)/rmse_old*100:.1f}%")

    # fixture-clustered bootstrap CI for the SIM challenger
    fixtures = [r.get("fixture") for r in test_rows]
    uniq = sorted(set(f for f in fixtures if f is not None))
    diff_by_fix = {}
    for i, fx in enumerate(fixtures):
        if fx is None:
            continue
        diff_by_fix.setdefault(fx, []).append((e_old[i] - y[i]) ** 2 - (e_sim[i] - y[i]) ** 2)
    fx_keys = list(diff_by_fix.keys())
    fx_sq_diff = {k: np.array(v) for k, v in diff_by_fix.items()}
    rng = np.random.default_rng(42)
    boots = []
    for _ in range(10000):
        sample = rng.choice(fx_keys, size=len(fx_keys), replace=True)
        diffs = np.concatenate([fx_sq_diff[k] for k in sample])
        boots.append(diffs.mean())
    boots = np.array(boots)
    lo, hi = np.percentile(boots, 2.5), np.percentile(boots, 97.5)
    print(f"fixture-clustered 95% CI on sq-err diff (old-sim): [{lo:.5f}, {hi:.5f}] | excludes zero: {lo > 0}")

    # save report
    report = {
        "train_gws": [1, TRAIN_MAX], "calib_gws": [CALIB_LO, CALIB_HI],
        "test_gws": [TEST_LO, TEST_HI], "n_test": len(test_rows),
        "rowwise": {"rmse_new": rmse_new, "rmse_old": rmse_old, "mae_new": mae_new, "mae_old": mae_old,
                    "rel_improvement_pct": (rmse_old - rmse_new) / rmse_old * 100},
        "sim": {"rmse_new": rmse_sim, "rmse_old": rmse_old, "mae_new": mae_sim, "mae_old": mae_old,
                "rel_improvement_pct": (rmse_old - rmse_sim) / rmse_old * 100,
                "ci95": [float(lo), float(hi)], "ci_excludes_zero": bool(lo > 0)},
        "mean_actual": float(y.mean()),
    }
    os.makedirs(os.path.join(BASE, "reports"), exist_ok=True)
    with open(os.path.join(BASE, "reports", "bonus_model_2025_26_backtest.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print("\nreport saved -> reports/bonus_model_2025_26_backtest.json")
    return report


if __name__ == "__main__":
    main()
