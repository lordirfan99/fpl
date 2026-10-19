#!/usr/bin/env python3
"""Walk-forward evaluation for the v4.4 learned candidate.

GW1-2 are warm-up; reported holdout is GW5-6. Cohorts are selected before
outcomes are read (cumulative prior minutes >= 90), so zero-point outcomes stay.
Legacy residuals are optional descriptive context only: their forecast provenance
and deadline capture cannot be established from the supplied CSV.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve()
ENGINE = HERE.parents[1]
sys.path.insert(0, str(ENGINE / "model"))
from competitive_v44_candidate import POSITIONS, _aggregate, predict_gameweek  # noqa: E402


def _metrics(rows):
    if not rows:
        return {"n": 0, "mae": None, "bias": None, "rmse": None, "spearman": None}
    errors = [float(row["pred"]) - float(row["actual"]) for row in rows]
    preds = [float(row["pred"]) for row in rows]
    actual = [float(row["actual"]) for row in rows]
    def rank(values):
        order = sorted(range(len(values)), key=values.__getitem__)
        out = [0.0] * len(values)
        index = 0
        while index < len(order):
            end = index + 1
            while end < len(order) and values[order[end]] == values[order[index]]:
                end += 1
            for item in order[index:end]: out[item] = (index + end - 1) / 2.0
            index = end
        return out
    rp, ra = rank(preds), rank(actual)
    mp, ma = statistics.fmean(rp), statistics.fmean(ra)
    den = math.sqrt(sum((x - mp) ** 2 for x in rp) * sum((x - ma) ** 2 for x in ra))
    return {"n": len(rows), "mae": round(statistics.fmean(abs(x) for x in errors), 6),
            "bias": round(statistics.fmean(errors), 6), "rmse": round(math.sqrt(statistics.fmean(x * x for x in errors)), 6),
            "spearman": None if den == 0 else round(sum((x - mp) * (y - ma) for x, y in zip(rp, ra)) / den, 6)}


def _load_history(path):
    rows = [json.loads(line) for line in pathlib.Path(path).read_text().splitlines() if line.strip()]
    seen = set()
    for row in rows:
        key = (int(row["gw"]), int(row["element"]))
        if key in seen:
            raise ValueError(f"duplicate gameweek/player row: {key}")
        seen.add(key)
    return rows


def _load_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _position(row):
    return POSITIONS.get(int(float(row.get("position", row.get("pos", 3)))), "MID")


def _prospective_baselines(history, target_gw, element, position):
    prior = [row for row in history if int(row["gw"]) < target_gw]
    own = [row for row in prior if int(row["element"]) == element]
    if sum(float(row.get("minutes", 0)) for row in own) < 90.0:
        return None
    position_rows = [row for row in prior if _position(row) == position]
    position_mean = statistics.fmean(float(row.get("total_points", 0)) for row in position_rows) if position_rows else 0.0
    ewma = _aggregate(own)["points"] if own else position_mean
    return {"position_prior": position_mean, "ewma_points": ewma}


def _promotion_gate(candidate_mae, baseline_maes):
    return (candidate_mae is not None and math.isfinite(candidate_mae)
            and bool(baseline_maes)
            and all(value is not None and math.isfinite(value) and candidate_mae <= value
                    for value in baseline_maes))


def evaluate(history_path, output_dir, residuals_path=None):
    history = _load_history(history_path)
    actual = {(int(row["gw"]), int(row["element"])): float(row["total_points"]) for row in history}
    rows = []
    prediction_rows = {}
    for gw in range(3, 7):
        predictions = predict_gameweek(history, gw)
        prediction_rows[gw] = predictions
        for prediction in predictions:
            key = (gw, prediction["element"])
            if key not in actual:
                continue
            baseline = _prospective_baselines(history, gw, prediction["element"], prediction["position"])
            if baseline is None:
                continue
            rows.append({**prediction, **baseline, "actual": actual[key], "cohort": "prior_minutes_ge_90"})
    holdout = [row for row in rows if row["gw"] in (5, 6)]
    warmup_eval = [row for row in rows if row["gw"] in (3, 4)]
    def view(subset):
        return {name: _metrics([{"pred": row[name], "actual": row["actual"]} for row in subset]) for name in ("predicted", "ewma_points", "position_prior")}
    residual_context = None
    if residuals_path:
        residual_rows = _load_csv(residuals_path)
        residual_context = {"rows": len(residual_rows), "provenance": "unverified", "used_for_promotion": False,
                            "note": "Supplied CSV has no verifiable forecast creation/deadline provenance; descriptive context only."}
    metrics = {"prospective_all_gws_3_6": view(rows), "warmup_eval_gw3_4": view(warmup_eval), "final_holdout_gw5_6": view(holdout)}
    candidate_mae = metrics["final_holdout_gw5_6"]["predicted"]["mae"]
    baselines = [metrics["final_holdout_gw5_6"][name]["mae"] for name in ("ewma_points", "position_prior")]
    gate = _promotion_gate(candidate_mae, baselines)
    report = {"candidate": "competitive-v4.4-walkforward-ridge", "data": {"history_rows": len(history), "warmup_gws": [1, 2], "evaluated_gws": [3, 4, 5, 6], "holdout_gws": [5, 6], "cohort": "prospective prior cumulative minutes >= 90; zero-point outcomes retained", "joined_rows": len(rows), "holdout_rows": len(holdout), "future_features_used": []},
              "metrics": metrics, "coverage": {"prediction_rows_by_gw": {str(gw): len(prediction_rows[gw]) for gw in prediction_rows}, "cohort_rows_by_gw": {str(gw): sum(row["gw"] == gw for row in rows) for gw in range(3, 7)}, "zero_actuals_in_cohort": sum(row["actual"] == 0 for row in rows)},
              "legacy_residual_context": residual_context, "promotion_gate": {"rule": "holdout candidate MAE must be <= both prospective EWMA and position-prior baselines", "candidate_mae": candidate_mae, "baseline_maes": baselines, "passed": gate},
              "promotion_verdict": "shadow_candidate_passes_numeric_gate" if gate else "shadow_only_no_promotion",
              "limitations": ["Only six finalized gameweeks are available; GW5-6 is a small retrospective evaluation tail, not an untouched prospective holdout; these weeks were inspected during candidate development.", "No fixture, opponent, price, injury, or lineup data is available.", "Captain output is intentionally omitted: ranking is not a feasible captain policy without prices and fixtures.", "The ridge strength is prespecified, not tuned on GW5-6.", "New players with no prior row are absent from the prospective universe rather than assigned faked GW1 metadata."]}
    output = pathlib.Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    (output / "evaluation.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = ["# FPL v4.4 learned candidate evaluation", "", "- Model: `competitive-v4.4-walkforward-ridge`", "- Training: each target is fit only from earlier gameweeks; GW1-2 warm-up", "- Cohort: prospective cumulative prior minutes >= 90; zero outcomes retained", "- Holdout: GW5-6", "", "| Cohort | Candidate MAE | EWMA baseline MAE | Position prior MAE | Candidate Spearman |", "|---|---:|---:|---:|---:|"]
    for label, subset in (("GW3-6", rows), ("GW3-4 warmup", warmup_eval), ("GW5-6 holdout", holdout)):
        data = view(subset)
        lines.append(f"| {label} | {data['predicted']['mae']} | {data['ewma_points']['mae']} | {data['position_prior']['mae']} | {data['predicted']['spearman']} |")
    lines += ["", f"- Promotion gate: **{'PASS' if gate else 'FAIL'}**; shadow status remains **no automatic promotion**.", "- Legacy residuals, when supplied, are unverified descriptive context and never a gate.", "- No captain policy is claimed.", ""]
    (output / "report.md").write_text("\n".join(lines))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", required=True)
    parser.add_argument("--residuals")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = evaluate(args.history, args.output_dir, args.residuals)
    print(json.dumps({"candidate": report["candidate"], "joined_rows": report["data"]["joined_rows"], "holdout": report["metrics"]["final_holdout_gw5_6"], "verdict": report["promotion_verdict"]}, indent=2))


if __name__ == "__main__":
    main()
