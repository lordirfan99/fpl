#!/usr/bin/env python3
"""Chronological offline evaluation for the v4.3 lagged candidate.

Inputs are explicit copies of finalized GW1-6 VM artifacts. This script never
fetches live/bootstrap data, reads current ``ep_next``, writes FPL, or mutates
repo data. Output is a JSON report plus a short Markdown report.
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
from competitive_v43_candidate import predict_gameweek  # noqa: E402


def _metrics(rows):
    if not rows:
        return {"n": 0, "mae": None, "bias": None, "rmse": None, "spearman": None}
    errors = [float(r["pred"]) - float(r["actual"]) for r in rows]
    preds = [float(r["pred"]) for r in rows]
    actual = [float(r["actual"]) for r in rows]
    def rank(values):
        order = sorted(range(len(values)), key=values.__getitem__)
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i + 1
            while j < len(order) and values[order[j]] == values[order[i]]:
                j += 1
            value = (i + j - 1) / 2.0
            for k in order[i:j]: out[k] = value
            i = j
        return out
    rp, ra = rank(preds), rank(actual)
    mp, ma = statistics.fmean(rp), statistics.fmean(ra)
    den = math.sqrt(sum((x - mp) ** 2 for x in rp) * sum((x - ma) ** 2 for x in ra))
    return {"n": len(rows), "mae": round(statistics.fmean(abs(x) for x in errors), 6),
            "bias": round(statistics.fmean(errors), 6), "rmse": round(math.sqrt(statistics.fmean(x*x for x in errors)), 6),
            "spearman": None if den == 0 else round(sum((x-mp)*(y-ma) for x,y in zip(rp,ra))/den, 6)}


def _load_history(path):
    return [json.loads(line) for line in pathlib.Path(path).read_text().splitlines() if line.strip()]


def _load_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _baseline_rows(rows, residuals):
    by_key = {(int(r["gw"]), int(r["element"])): r for r in residuals}
    out = []
    for row in rows:
        key = (row["gw"], row["element"])
        if key in by_key:
            out.append({"pred": float(by_key[key]["predicted"]), "actual": row["actual"]})
    return out


def evaluate(history_path, residuals_path, output_dir, v42_path=None):
    history = _load_history(history_path)
    actual = {(int(r["gw"]), int(r["element"])): float(r["total_points"]) for r in history}
    candidate = []
    for gw in range(1, 7):
        predictions = predict_gameweek(history, gw)
        for prediction in predictions:
            key = (gw, prediction["element"])
            if key in actual:
                candidate.append({**prediction, "actual": actual[key]})
    residuals = _load_csv(residuals_path)
    residual_by_key = {(int(r["gw"]), int(r["element"])): r for r in residuals if 1 <= int(r["gw"]) <= 6}
    joined = [{**row, "baseline_pred": float(residual_by_key[(row["gw"], row["element"])] ["predicted"])}
              for row in candidate if (row["gw"], row["element"]) in residual_by_key]
    def view(subset):
        return {"candidate": _metrics([{"pred": r["predicted"], "actual": r["actual"]} for r in subset]),
                "legacy_timestamp_baseline": _metrics([{"pred": r["baseline_pred"], "actual": r["actual"]} for r in subset]),
                "zero_baseline": _metrics([{"pred": 0.0, "actual": r["actual"]} for r in subset])}
    all_rows = joined
    played = [r for r in joined if r["actual"] > 0 and r["p_60_plus"] >= 0.5]
    decision = []
    for gw in range(1, 7):
        gw_rows = [r for r in joined if r["gw"] == gw]
        decision.extend(sorted(gw_rows, key=lambda r: r["predicted"], reverse=True)[:100])
    ranking = {str(gw): _metrics([{"pred": r["predicted"], "actual": r["actual"]} for r in joined if r["gw"] == gw]) for gw in range(1, 7)}
    captains = []
    for gw in range(1, 7):
        gw_rows = [r for r in joined if r["gw"] == gw]
        if gw_rows:
            pick = max(gw_rows, key=lambda r: r["predicted"])
            captains.append({"gw": gw, "selected_element": pick["element"], "predicted": pick["predicted"], "actual": pick["actual"], "rank": 1})
    captain_summary = {"weeks": len(captains), "top1_mean_actual": round(statistics.fmean(row["actual"] for row in captains), 6) if captains else None,
                       "top1_positive_weeks": sum(row["actual"] > 0 for row in captains)}
    v42_metrics = None
    if v42_path:
        v42 = _load_csv(v42_path)
        v42_by_key = {(int(r["gw"]), int(r["element"])): r for r in v42 if 1 <= int(r["gw"]) <= 6}
        common = [r for r in joined if (r["gw"], r["element"]) in v42_by_key]
        v42_metrics = _metrics([{"pred": float(v42_by_key[(r["gw"], r["element"])] ["predicted"]), "actual": r["actual"]} for r in common])
    report = {"candidate": "competitive-v4.3-lagged-shrink", "data": {"history_rows": len(history), "evaluated_gws": list(range(1, 7)), "finalized_only": True, "future_features_used": [], "baseline_source": "timestamp-captured residuals.csv", "joined_rows": len(joined)},
              "metrics": {"all_joined": view(all_rows), "played_decision_like": view(played), "top100_per_gw": view(decision)},
              "ranking_by_gw": ranking, "captain_top_rank": captains, "captain_summary": captain_summary,
              "v42_shadow_common_cohort": v42_metrics,
              "coverage": {"candidate_rows": len(candidate), "joined_rows": len(joined), "training_rows_gt0": sum(r["training_rows"] > 0 for r in candidate), "fallback_rows": sum(r["fallback"] for r in candidate), "defensive_probability_rows": sum("p_defensive_threshold" in r for r in candidate)},
              "promotion_verdict": "shadow_only_no_promotion",
              "limitations": ["GW1 uses position priors because no lagged rows exist.", "No fixture/opponent or price data was available in the finalized history artifact.", "Captain top-pick is a ranking diagnostic, not hindsight best-starter performance.", "The legacy baseline is timestamp-captured and its cohort is an intersection, not a reconstructed forecast."]}
    output_dir = pathlib.Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "evaluation.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = ["# FPL v4.3 candidate evaluation", "", "- Candidate: `competitive-v4.3-lagged-shrink`", f"- Finalized gameweeks: GW1–6; joined rows: {len(joined)}", "- Decision: **shadow only; no promotion**", "", "## Metrics", "", "| Cohort | Candidate MAE | Legacy captured MAE | Zero MAE | Candidate Spearman |", "|---|---:|---:|---:|---:|"]
    for name, data in (("All", view(all_rows)), ("Played/decsion-like", view(played)), ("Top 100/GW", view(decision))):
        lines.append(f"| {name} | {data['candidate']['mae']} | {data['legacy_timestamp_baseline']['mae']} | {data['zero_baseline']['mae']} | {data['candidate']['spearman']} |")
    lines += ["", "## Ranking and captain diagnostics", "", f"- Candidate top-pick captain mean actual: {captain_summary['top1_mean_actual']} over {captain_summary['weeks']} finalized weeks; positive weeks: {captain_summary['top1_positive_weeks']}."]
    if v42_metrics:
        lines.append(f"- v4.2 common-cohort MAE: {v42_metrics['mae']} over {v42_metrics['n']} rows (descriptive, not a promotion gate).")
    lines += ["", "## Safety and limitations", "", "- Predictions are trained only on rows from earlier gameweeks; no current/bootstrap features are used.", "- Defensive contribution uses a probability of threshold crossing, not mean >= threshold.", "- Fallback/training coverage is recorded in `evaluation.json`.", "- GW7 is excluded because the supplied audit marks it unfinished."]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", required=True)
    parser.add_argument("--residuals", required=True)
    parser.add_argument("--v42", help="Optional timestamp-captured v4.2 residuals for common-cohort comparison")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    report = evaluate(args.history, args.residuals, args.output_dir, args.v42)
    print(json.dumps({"candidate": report["candidate"], "joined_rows": report["data"]["joined_rows"], "metrics": report["metrics"], "coverage": report["coverage"], "verdict": report["promotion_verdict"]}, indent=2))


if __name__ == "__main__":
    main()
