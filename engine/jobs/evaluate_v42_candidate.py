#!/usr/bin/env python3
"""Evaluate and explicitly promote/rollback the V4.2 projection candidate."""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import math
import os

from project_paths import resolve_project_root

BASE = str(resolve_project_root(__file__))
PROCESSED = os.path.join(BASE, "data", "processed")
STATE_FILE = os.path.join(PROCESSED, "v42_candidate_state.json")
REGISTRY_FILE = os.path.join(PROCESSED, "model_registry.json")


def _rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _metrics(pairs):
    if not pairs:
        return {"n": 0, "mae": None, "bias": None, "spearman": None}
    errors = [pred - actual for pred, actual in pairs]
    spearman = None
    try:
        import pandas as pd
        spearman = pd.Series([p for p, _ in pairs]).corr(
            pd.Series([a for _, a in pairs]), method="spearman")
        if spearman is not None and math.isnan(spearman):
            spearman = None
    except Exception:
        # Dependency-free average-rank fallback keeps the evaluator usable in
        # the minimal production recovery environment.
        def ranks(values):
            order = sorted(range(len(values)), key=lambda index: values[index])
            output = [0.0] * len(values)
            cursor = 0
            while cursor < len(order):
                end = cursor + 1
                while end < len(order) and values[order[end]] == values[order[cursor]]:
                    end += 1
                rank = (cursor + end - 1) / 2.0
                for index in order[cursor:end]:
                    output[index] = rank
                cursor = end
            return output
        left, right = ranks([p for p, _ in pairs]), ranks([a for _, a in pairs])
        left_mean, right_mean = sum(left) / len(left), sum(right) / len(right)
        numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
        den_left = sum((a - left_mean) ** 2 for a in left)
        den_right = sum((b - right_mean) ** 2 for b in right)
        if den_left > 0 and den_right > 0:
            spearman = numerator / math.sqrt(den_left * den_right)
    return {"n": len(pairs),
            "mae": round(sum(abs(e) for e in errors) / len(errors), 4),
            "bias": round(sum(errors) / len(errors), 4),
            "spearman": None if spearman is None else round(float(spearman), 4)}


def _state_brier(rows, candidate):
    if not rows:
        return None
    total = 0.0
    for row in rows:
        minutes = _f(row.get("minutes"))
        actual = (1.0 if minutes <= 0 else 0.0,
                  1.0 if 0 < minutes < 60 else 0.0,
                  1.0 if minutes >= 60 else 0.0)
        if candidate:
            probs = (_f(row.get("p_dnp")), _f(row.get("p_1_59")),
                     _f(row.get("p_60_plus")))
        else:
            p_start = max(0.0, min(1.0, _f(row.get("p_start"))))
            expected = max(0.0, min(90.0, _f(row.get("expected_minutes"))))
            p_appear = max(p_start, min(0.98, expected / 70.0))
            probs = (1.0 - p_appear, max(0.0, p_appear - p_start), p_start)
        total += sum((prob - truth) ** 2 for prob, truth in zip(probs, actual)) / 3.0
    return round(total / len(rows), 5)


def evaluate(champion_rows, candidate_rows, cfg, policy=None):
    champion = {(int(r["gw"]), int(r["element"])): r for r in champion_rows}
    candidate = {(int(r["gw"]), int(r["element"])): r for r in candidate_rows}
    keys = sorted(champion.keys() & candidate.keys())
    joined = [(champion[key], candidate[key]) for key in keys]
    gws = sorted({key[0] for key in keys})
    all_champion = _metrics([(_f(a.get("predicted")), _f(a.get("actual"))) for a, _ in joined])
    all_candidate = _metrics([(_f(b.get("predicted")), _f(b.get("actual"))) for _, b in joined])
    decision = [(a, b) for a, b in joined
                if _f(b.get("p_60_plus")) >= 0.65 or _f(b.get("predicted")) >= 4.0]
    decision_champion = _metrics([(_f(a.get("predicted")), _f(a.get("actual"))) for a, _ in decision])
    decision_candidate = _metrics([(_f(b.get("predicted")), _f(b.get("actual"))) for _, b in decision])
    brier_champion = _state_brier([a for a, _ in joined], False)
    brier_candidate = _state_brier([b for _, b in joined], True)

    position_regressions = {}
    for position in sorted({str(b.get("pos") or "?") for _, b in joined}):
        subset = [(a, b) for a, b in joined if str(b.get("pos") or "?") == position]
        ma = _metrics([(_f(a.get("predicted")), _f(a.get("actual"))) for a, _ in subset])
        mb = _metrics([(_f(b.get("predicted")), _f(b.get("actual"))) for _, b in subset])
        if ma["mae"] is not None and mb["mae"] is not None:
            position_regressions[position] = round(mb["mae"] - ma["mae"], 4)

    interval_rows = [b for _, b in joined if b.get("floor") not in (None, "")
                     and b.get("upside") not in (None, "")]
    coverage = None
    if interval_rows:
        coverage = sum(_f(r["floor"]) <= _f(r["actual"]) <= _f(r["upside"])
                       for r in interval_rows) / len(interval_rows)
        coverage = round(coverage, 4)

    min_gws = int(cfg.get("min_live_gws", 6))
    min_rows = int(cfg.get("min_rows", 500))
    improvement_needed = max(0.10, 0.03 * float(decision_champion.get("mae") or 0.0))
    checks = {
        "live_gws": len(gws) >= min_gws,
        "rows": len(joined) >= min_rows,
        "decision_mae": (decision_candidate.get("mae") is not None
                         and decision_candidate["mae"] <= decision_champion["mae"] - improvement_needed),
        "bias": all_candidate.get("bias") is not None and abs(all_candidate["bias"]) <= 0.25,
        "minutes_brier": (brier_candidate is not None and brier_champion is not None
                          and brier_candidate <= brier_champion * 0.95),
        "position_safety": all(delta <= 0.10 for delta in position_regressions.values()),
        "rank_safety": (all_candidate.get("spearman") is not None
                        and all_champion.get("spearman") is not None
                        and all_candidate["spearman"] >= all_champion["spearman"] - 0.02),
        "coverage": coverage is not None and 0.72 <= coverage <= 0.88,
        "policy_safety": (bool(policy)
                          and len(policy.get("evaluated_gws") or []) >= min_gws
                          and float(policy.get("candidate_total", -1)) >= float(
                              policy.get("champion_total", 0))),
    }
    return {
        "candidate": "competitive-v4.2-shadow", "champion": "competitive-v4.0",
        "evaluated_gws": gws, "n_rows": len(joined),
        "all_players": {"champion": all_champion, "candidate": all_candidate},
        "decision_cohort": {"n": len(decision), "champion": decision_champion,
                            "candidate": decision_candidate,
                            "required_mae_improvement": round(improvement_needed, 4)},
        "minutes_brier": {"champion": brier_champion, "candidate": brier_candidate},
        "position_mae_delta": position_regressions, "interval_coverage": coverage,
        "decision_policy": policy,
        "checks": checks, "eligible_for_owner_approval": all(checks.values()),
    }


def policy_scores(processed, candidate_rows):
    """Replay locked lineup/captain/transfer outcomes for both policies."""
    actual_by_gw = {}
    for row in candidate_rows:
        actual_by_gw.setdefault(int(row["gw"]), {})[int(row["element"])] = _f(row.get("actual"))
    output = {"champion_total": 0.0, "candidate_total": 0.0,
              "champion_transfer_net": 0.0, "candidate_transfer_net": 0.0,
              "evaluated_gws": []}
    for gw, actual in sorted(actual_by_gw.items()):
        plan_path = os.path.join(processed, f"plan_gw{gw}.json")
        shadow_path = os.path.join(processed, f"v42_shadow_gw{gw}.json")
        if not os.path.exists(plan_path) or not os.path.exists(shadow_path):
            continue
        try:
            with open(plan_path, encoding="utf-8") as handle:
                plan = json.load(handle)
            with open(shadow_path, encoding="utf-8") as handle:
                shadow = json.load(handle)
        except (OSError, ValueError, TypeError):
            continue
        champion_lineup = [int(p["id"]) for p in plan.get("target_starters", [])]
        candidate_lineup = [int(value) for value in shadow.get("lineup_ids", [])]
        champion_cap = int((plan.get("captain") or {}).get("id") or 0)
        candidate_cap = int(shadow.get("captain_id") or 0)
        if len(champion_lineup) != 11 or len(candidate_lineup) != 11:
            continue
        output["champion_total"] += sum(actual.get(pid, 0) for pid in champion_lineup)
        output["candidate_total"] += sum(actual.get(pid, 0) for pid in candidate_lineup)
        output["champion_total"] += actual.get(champion_cap, 0)
        output["candidate_total"] += actual.get(candidate_cap, 0)
        champion_hits = sum(1 for transfer in plan.get("transfers", [])
                            if transfer.get("hit"))
        output["champion_total"] -= 4 * champion_hits
        output["candidate_total"] -= 4 * int((shadow.get("first_week") or {}).get("hits") or 0)
        for transfer in plan.get("transfers", []):
            incoming = int(transfer.get("element_in") or 0)
            outgoing = int(transfer.get("element_out") or 0)
            output["champion_transfer_net"] += actual.get(incoming, 0) - actual.get(outgoing, 0)
            if transfer.get("hit"):
                output["champion_transfer_net"] -= 4
        first = shadow.get("first_week") or {}
        for transfer in first.get("transfers", []):
            incoming = int(transfer.get("element_in") or 0)
            outgoing = int(transfer.get("element_out") or 0)
            output["candidate_transfer_net"] += actual.get(incoming, 0) - actual.get(outgoing, 0)
        output["candidate_transfer_net"] -= 4 * int(first.get("hits") or 0)
        output["evaluated_gws"].append(gw)
    for key in ("champion_total", "candidate_total", "champion_transfer_net",
                "candidate_transfer_net"):
        output[key] = round(output[key], 2)
    return output if output["evaluated_gws"] else None


def _save(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve-candidate", choices=["competitive-v4.2-shadow"])
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    with open(os.path.join(BASE, "config", "settings.json"), encoding="utf-8") as handle:
        settings = json.load(handle)
    cfg = settings.get("v42_candidate") or {}
    champion_rows = _rows(os.path.join(PROCESSED, "residuals.csv"))
    candidate_rows = _rows(os.path.join(PROCESSED, "v42_residuals.csv"))
    report = evaluate(champion_rows, candidate_rows, cfg,
                      policy_scores(PROCESSED, candidate_rows))
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state = {"updated_at": now, **report}
    if args.rollback:
        registry = {"active_projection": "competitive-v4.0",
                    "previous_projection": "competitive-v4.2",
                    "changed_at": now, "reason": "explicit_owner_rollback"}
        _save(REGISTRY_FILE, registry)
        state["owner_status"] = "rolled_back"
    elif args.approve_candidate:
        if not report["eligible_for_owner_approval"]:
            print(json.dumps(report, indent=2))
            print("V4.2 is not eligible for owner approval; production unchanged.")
            return 2
        registry = {"active_projection": "competitive-v4.2",
                    "previous_projection": "competitive-v4.0",
                    "changed_at": now, "reason": "explicit_owner_approval"}
        _save(REGISTRY_FILE, registry)
        state["owner_status"] = "approved"
    else:
        state["owner_status"] = "awaiting_eligibility" if not report[
            "eligible_for_owner_approval"] else "awaiting_owner_approval"
    _save(STATE_FILE, state)
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
