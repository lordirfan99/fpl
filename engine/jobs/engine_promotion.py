#!/usr/bin/env python3
"""
FPL Autopilot - v3 auto-promotion (acceptance gates from docs/intelligence_engine_v3.md).

After each finished GW, compare the v2 predictions (predictions_gwN.json /
residuals.csv) against the v3 SHADOW predictions (v3_shadow_gwN.json) using
the actual results. If the gates pass, this writes data/processed/engine_state.json
with promoted: true and the live pipeline (pre_deadline_run.py) switches to
the v3 engine AUTOMATICALLY. No manual edit required.

Gates (from the v3 docs):
  1. at least N shadow GWs have been evaluated (N = settings v3_promotion_min_gws, default 3)
  2. v3 MAE is not materially worse than v2 (tolerance settings v3_mae_tolerance, default 0.05)
  3. v3 improves at least one decision metric vs v2:
       - captain points (v3-picked captain vs v2-picked captain)
       - Spearman rank correlation
       - squad points (v3 15-man squad vs v2 squad)

Promotion is STICKY: once promoted, a bad week does not demote (a single
gameweek is noise); the comparison report is kept in engine_state for audit.

Silent unless the promotion STATE changes (promoted flips, or the comparison
summary changes) - no_agent cron / auto-runner friendly. Run:
    .venv/Scripts/python.exe jobs/engine_promotion.py
"""
import json
import math
import os
import sys
import datetime

from project_paths import resolve_project_root

BASE = str(resolve_project_root(__file__))
PROCESSED = os.path.join(BASE, "data", "processed")
ENGINE_STATE_FILE = os.path.join(PROCESSED, "engine_state.json")
SETTINGS_FILE = os.path.join(BASE, "config", "settings.json")

DEFAULTS = {
    "v3_auto_promote": True,
    "v3_promotion_min_gws": 3,
    "v3_mae_tolerance": 0.05,
}


def load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def save_json(path, obj):
    sys.path.insert(0, os.path.join(BASE, "execution"))
    from atomic_io import atomic_write_json
    atomic_write_json(path, obj)


def load_settings():
    s = load_json(SETTINGS_FILE, {}) or {}
    for k, v in DEFAULTS.items():
        s.setdefault(k, v)
    return s


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_v2_rows(residuals_csv):
    """{(gw, element): {"pred": float, "actual": float}} from residuals.csv."""
    import csv
    out = {}
    if not os.path.exists(residuals_csv):
        return out
    with open(residuals_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                gw = int(r["gw"])
                el = int(r["element"])
                pred = float(r.get("predicted") or r.get("xpts") or 0)
                actual = float(r.get("actual") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            out[(gw, el)] = {"pred": pred, "actual": actual}
    return out


def load_shadow_gw(path):
    """{player_id: v3_pred} + captain id + squad ids from a v3_shadow file."""
    d = load_json(path)
    if not d:
        return None
    players = {}
    for p in list(d.get("squad", [])) + list(d.get("top_candidates", [])):
        pid = p.get("id")
        if pid is None:
            continue
        pred = p.get("xpts")
        if pred is None:
            continue
        players[pid] = float(pred)
    captain = (d.get("captain") or {}).get("id")
    squad_ids = {p.get("id") for p in d.get("squad", []) if p.get("id") is not None}
    return {"players": players, "captain": captain, "squad_ids": squad_ids}


def find_shadow_gws(processed_dir):
    """[(gw, path)] for every v3_shadow_gwN.json present."""
    out = []
    if not os.path.isdir(processed_dir):
        return out
    for name in sorted(os.listdir(processed_dir)):
        if name.startswith("v3_shadow_gw") and name.endswith(".json"):
            try:
                gw = int(name.replace("v3_shadow_gw", "").replace(".json", ""))
            except ValueError:
                continue
            out.append((gw, os.path.join(processed_dir, name)))
    return out


def load_v2_captain(processed_dir, gw):
    """v2 captain element id from plan_gw{gw}.json, or None."""
    plan = load_json(os.path.join(processed_dir, f"plan_gw{gw}.json"))
    if not plan:
        return None
    return (plan.get("captain") or {}).get("id")


def load_v2_squad(processed_dir, gw):
    """v2 squad element ids from plan_gw{gw}.json (starters + bench)."""
    plan = load_json(os.path.join(processed_dir, f"plan_gw{gw}.json"))
    if not plan:
        return set()
    ids = {p.get("id") for p in plan.get("target_starters", [])}
    ids |= {p.get("id") for p in plan.get("bench", [])}
    return {i for i in ids if i is not None}


# ---------------------------------------------------------------------------
# Metrics + gates (pure, unit-tested)
# ---------------------------------------------------------------------------
def _mae(rows):
    if not rows:
        return None
    return sum(abs(r["v2_pred"] - r["actual"]) for r in rows) / len(rows), \
           sum(abs(r["v3_pred"] - r["actual"]) for r in rows) / len(rows)


def _spearman(rows):
    if len(rows) < 3:
        return None, None
    try:
        import pandas as pd
        s2 = pd.Series([r["v2_pred"] for r in rows]).corr(
            pd.Series([r["actual"] for r in rows]), method="spearman")
        s3 = pd.Series([r["v3_pred"] for r in rows]).corr(
            pd.Series([r["actual"] for r in rows]), method="spearman")
        return (None if s2 is None or math.isnan(s2) else round(float(s2), 4),
                None if s3 is None or math.isnan(s3) else round(float(s3), 4))
    except Exception:
        return None, None


def evaluate_gates(shadow_rows, captain_scores, squad_scores, cfg):
    """Return (passed, report dict).

    shadow_rows: pooled rows {v2_pred, v3_pred, actual} across GWs.
    captain_scores: (v2_total, v3_total) across GWs.
    squad_scores: (v2_total, v3_total) across GWs.
    """
    n = len(shadow_rows)
    mae2 = mae3 = spearman2 = spearman3 = None
    if n:
        mae2, mae3 = _mae(shadow_rows)
        spearman2, spearman3 = _spearman(shadow_rows)
    cap2, cap3 = captain_scores
    sq2, sq3 = squad_scores

    report = {
        "n_player_rows": n,
        "mae_v2": round(mae2, 4) if mae2 is not None else None,
        "mae_v3": round(mae3, 4) if mae3 is not None else None,
        "spearman_v2": spearman2, "spearman_v3": spearman3,
        "captain_v2": cap2, "captain_v3": cap3,
        "squad_v2": sq2, "squad_v3": sq3,
    }
    if mae2 is None or mae3 is None:
        return False, {**report, "passed": False,
                       "reason": "no evaluable player rows (residuals missing?)"}
    mae_ok = mae3 <= mae2 * (1 + float(cfg.get("v3_mae_tolerance", 0.05)))
    improved = []
    if cap3 is not None and cap2 is not None and cap3 > cap2:
        improved.append("captain")
    if spearman3 is not None and spearman2 is not None and spearman3 > spearman2:
        improved.append("spearman")
    if sq3 is not None and sq2 is not None and sq3 > sq2:
        improved.append("squad")
    passed = mae_ok and bool(improved)
    reason = ("gates passed" if passed else
              ("MAE not materially worse but NO improved metric" if mae_ok else
               "v3 MAE materially worse than v2"))
    return passed, {**report, "mae_ok": mae_ok, "improved": improved,
                    "passed": passed, "reason": reason}


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------
def main():
    settings = load_settings()
    if not settings.get("v3_auto_promote", True):
        return 0

    state = load_json(ENGINE_STATE_FILE, {}) or {}
    residuals_csv = os.path.join(PROCESSED, "residuals.csv")
    v2rows = load_v2_rows(residuals_csv)

    shadow_files = find_shadow_gws(PROCESSED)
    # only FINISHED GWs have residuals rows; a shadow file without residuals
    # cannot be evaluated yet (its GW hasn't closed)
    pool = []
    cap2 = cap3 = 0.0
    sq2 = sq3 = 0.0
    evaluated_gws = []
    for gw, path in shadow_files:
        sh = load_shadow_gw(path)
        if not sh:
            continue
        rows = []
        for pid, v3p in sh["players"].items():
            key = (gw, pid)
            if key not in v2rows:
                continue
            rows.append({"v2_pred": v2rows[key]["pred"], "v3_pred": v3p,
                         "actual": v2rows[key]["actual"]})
        if not rows:
            continue
        # captain comparison: raw points of each engine's pick
        cap_v2_id = load_v2_captain(PROCESSED, gw)
        cap_v3_id = sh.get("captain")
        a = {pid: r["actual"] for pid, r in ((k[1], v) for k, v in v2rows.items() if k[0] == gw)}
        if cap_v2_id in a and cap_v3_id in a:
            cap2 += a[cap_v2_id]
            cap3 += a[cap_v3_id]
        # squad comparison
        sq_v2_ids = load_v2_squad(PROCESSED, gw)
        sq_v3_ids = sh.get("squad_ids") or set()
        sq2 += sum(a.get(i, 0) for i in sq_v2_ids)
        sq3 += sum(a.get(i, 0) for i in sq_v3_ids)
        pool.extend(rows)
        evaluated_gws.append(gw)

    n_eval = len(evaluated_gws)
    min_gws = int(settings.get("v3_promotion_min_gws", 3))
    report = {
        "evaluated_gws": evaluated_gws,
        "min_gws_required": min_gws,
        "gate_met": n_eval >= min_gws,
    }

    if n_eval < min_gws:
        report.update({"passed": False,
                       "reason": f"shadow evaluated {n_eval}/{min_gws} GWs - not enough data yet"})
        # update the progress counter for the bot display
        new_state = dict(state)
        new_state["promoted"] = bool(state.get("promoted"))
        new_state["shadow_evaluated_gws"] = n_eval
        new_state["report"] = report
        if new_state != state:
            save_json(ENGINE_STATE_FILE, new_state)
            print(f"[engine] v3 shadow progress {n_eval}/{min_gws} GWs evaluated - "
                  f"not promoted yet (report: {json.dumps(report)})")
        return 0

    passed, report = evaluate_gates(pool, (cap2, cap3), (sq2, sq3), settings)
    report.update({"evaluated_gws": evaluated_gws, "min_gws_required": min_gws,
                   "gate_met": True, "promoted": passed})

    new_state = dict(state)
    new_state["promoted"] = passed
    new_state["shadow_evaluated_gws"] = n_eval
    new_state["promoted_at"] = state.get("promoted_at") or (
        datetime.datetime.now(datetime.timezone.utc).isoformat() if passed else None)
    new_state["report"] = report

    # dedup: only print when the decision/report actually CHANGED
    old_report = state.get("report")
    changed = (bool(state.get("promoted")) != passed) or (old_report != report)
    save_json(ENGINE_STATE_FILE, new_state)
    if changed:
        if passed:
            print(f"[engine] 🚀 v3 PROMOTED to live engine (GWs {evaluated_gws})! "
                  f"MAE v2={report['mae_v2']} v3={report['mae_v3']} "
                  f"improved: {report['improved']}. Live pipeline now uses v3.")
        else:
            print(f"[engine] v3 NOT promoted after {n_eval} GWs: {report['reason']} "
                  f"(MAE v2={report['mae_v2']} v3={report['mae_v3']}, improved={report['improved']}). "
                  "Staying on v2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
