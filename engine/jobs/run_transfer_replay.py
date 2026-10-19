#!/usr/bin/env python3
"""Leakage-safe dynamic transfer replay using archived pre-deadline catalogs."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Any

HERE = pathlib.Path(__file__).resolve()
ENGINE = HERE.parents[1]
sys.path.insert(0, str(ENGINE / "model"))
from competitive_two_stage_candidate import CONFIGS, predict_gameweek  # noqa: E402
from competitive_v44_candidate import predict_gameweek as predict_ridge  # noqa: E402

GWS = (3, 4, 5, 6)
POS = (1, 2, 3, 4)
# Fixed priors for players absent from prior event history; never read from scored GWs.
NEW_PLAYER_PRIOR = {1: 2.60, 2: 2.80, 3: 3.30, 4: 3.30}


@dataclass
class Holding:
    price: int
    purchase: int


def load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    keys = {(int(r["gw"]), int(r["element"])) for r in rows}
    if len(keys) != len(rows):
        raise ValueError("duplicate history row")
    return rows


def active_catalog(path: pathlib.Path) -> dict[int, dict[str, Any]]:
    data = json.loads(path.read_text())
    result = {}
    for row in data["elements"]:
        # Conservative pre-deadline availability gate: do not buy unavailable or
        # explicitly ruled-out players. Chance 0 is blocked even if status is stale.
        if row.get("status") != "a" or row.get("chance_of_playing_next_round") == 0:
            continue
        result[int(row["id"])] = row
    return result


def legal_squad(ids: list[int], catalog: dict[int, dict[str, Any]]) -> bool:
    if len(ids) != 15 or len(set(ids)) != 15:
        return False
    counts = {p: sum(int(catalog[i]["element_type"]) == p for i in ids) for p in POS}
    return counts == {1: 2, 2: 5, 3: 5, 4: 3} and all(
        sum(int(catalog[i]["team"]) == club for i in ids) <= 3
        for club in {int(catalog[i]["team"]) for i in ids}
    )


def legal_xi(ids: list[int], catalog: dict[int, dict[str, Any]]) -> bool:
    counts = {p: sum(int(catalog[i]["element_type"]) == p for i in ids) for p in POS}
    return len(ids) == 11 and counts[1] == 1 and counts[2] >= 3 and counts[3] >= 2 and counts[4] >= 1


def lineup(squad: list[int], predictions: dict[int, float], catalog: dict[int, dict[str, Any]]) -> tuple[list[int], list[int], int, int, float]:
    best: tuple[tuple[float, tuple[int, ...]], tuple[list[int], list[int], int, int, float]] | None = None
    for xi in itertools.combinations(squad, 11):
        if not legal_xi(list(xi), catalog):
            continue
        captain = max(xi, key=lambda i: (predictions[i], -i))
        vice = max((i for i in xi if i != captain), key=lambda i: (predictions[i], -i))
        objective = sum(predictions[i] for i in xi) + predictions[captain]
        candidate = (list(xi), [i for i in squad if i not in xi], captain, vice, objective)
        key = (objective, tuple(sorted(xi)))
        if best is None or key > best[0]:
            best = (key, candidate)
    if best is None:
        raise AssertionError("no legal XI")
    return best[1]


def sale_value(holding: Holding, current_price: int) -> int:
    gain = current_price - holding.purchase
    return holding.purchase + (gain // 2 if gain > 0 else 0)


def choose_squad(candidates: dict[int, dict[str, Any]], predictions: dict[int, float], budget: int, current: list[int] | None, holdings: dict[int, Holding] | None) -> tuple[list[int], list[int], list[int], int, int, int, int]:
    """Solve squad + XI + captain jointly; one transfer is enforced after GW3."""
    import pulp
    ids = sorted(candidates)
    prob = pulp.LpProblem("fpl_transfer_replay", pulp.LpMaximize)
    x = {i: pulp.LpVariable(f"x_{i}", cat="Binary") for i in ids}
    y = {i: pulp.LpVariable(f"y_{i}", cat="Binary") for i in ids}
    c = {i: pulp.LpVariable(f"c_{i}", cat="Binary") for i in ids}
    for i in ids:
        prob += y[i] <= x[i]
        prob += c[i] <= y[i]
        if not candidates[i].get("_can_play", True):
            prob += y[i] == 0
            prob += c[i] == 0
    prob += sum(x.values()) == 15
    prob += sum(y.values()) == 11
    prob += sum(c.values()) == 1
    for p, count in ((1, 2), (2, 5), (3, 5), (4, 3)):
        prob += sum(x[i] for i in ids if int(candidates[i]["element_type"]) == p) == count
    for p, minimum in ((1, 1), (2, 3), (3, 2), (4, 1)):
        prob += sum(y[i] for i in ids if int(candidates[i]["element_type"]) == p) >= minimum
    prob += sum(y[i] for i in ids if int(candidates[i]["element_type"]) == 1) == 1
    for club in {int(candidates[i]["team"]) for i in ids}:
        prob += sum(x[i] for i in ids if int(candidates[i]["team"]) == club) <= 3
    cost = {i: int(candidates[i]["now_cost"]) for i in ids}
    if current is None:
        prob += sum(cost[i] * x[i] for i in ids) <= budget
    else:
        # Retain all but at most one current player; sale proceeds use the holding ledger.
        held = set(current)
        prob += sum(x[i] for i in held if i in x) >= 14
        proceeds = sum(sale_value(holdings[i], cost[i]) * (1 - x[i]) for i in held if i in x)
        purchases = sum(cost[i] * x[i] for i in ids if i not in held)
        prob += purchases <= budget + proceeds
    prob += sum(predictions[i] * y[i] + predictions[i] * c[i] for i in ids)
    if prob.solve(pulp.PULP_CBC_CMD(msg=False)) != pulp.LpStatusOptimal:
        raise AssertionError("no legal affordable squad")
    squad = [i for i in ids if x[i].value() > 0.5]
    xi = [i for i in ids if y[i].value() > 0.5]
    captain = next(i for i in ids if c[i].value() > 0.5)
    vice = max((i for i in xi if i != captain), key=lambda i: (predictions[i], -i))
    bench = [i for i in squad if i not in xi]
    spent = sum(cost[i] for i in squad)
    if current is None:
        newbank = budget - spent
    else:
        out = set(current) - set(squad)
        incoming = set(squad) - set(current)
        if len(out) > 1 or len(incoming) > 1:
            raise AssertionError("more than one transfer")
        newbank = budget + (sale_value(holdings[next(iter(out))], cost[next(iter(out))]) if out else 0) - (cost[next(iter(incoming))] if incoming else 0)
    return squad, xi, bench, captain, vice, spent, newbank


def score(xi: list[int], bench: list[int], captain: int, vice: int, gw: int, actual: dict[tuple[int, int], dict[str, Any]], positions: dict[int, int]) -> dict[str, Any]:
    minutes = lambda i: float(actual[(gw, i)]["minutes"])
    points = lambda i: float(actual[(gw, i)]["total_points"])
    final = set(xi)
    subs = []
    missing = [i for i in xi if minutes(i) == 0]
    available = [i for i in bench if minutes(i) > 0]
    for number in range(min(len(missing), len(available)) + 1):
        for incoming in itertools.combinations(available, number):
            for outgoing in itertools.combinations(missing, number):
                candidate = (set(xi) - set(outgoing)) | set(incoming)
                if legal_xi(list(candidate), {i: {"element_type": positions[i], "team": 0} for i in candidate}):
                    final = candidate
                    subs = [list(pair) for pair in zip(outgoing, incoming)]
        if subs:
            break
    effective = captain if minutes(captain) > 0 else vice if minutes(vice) > 0 else None
    return {"base": sum(points(i) for i in final), "captain_extra": points(effective) if effective else 0.0, "total": sum(points(i) for i in final) + (points(effective) if effective else 0.0), "effective_captain": effective, "subs": subs}


def model_predictions(history: list[dict[str, Any]], gw: int, catalog: dict[int, dict[str, Any]], model: str) -> dict[int, float]:
    if model == "two_stage_bayes":
        cfg = next(c for c in CONFIGS if c.name == model)
        rows = predict_gameweek(history, gw, cfg)
        values = {int(r["element"]): float(r["predicted"]) for r in rows}
    elif model == "ridge":
        values = {int(r["element"]): float(r["predicted"]) for r in predict_ridge(history, gw)}
    else:
        values = {}
    prior_ids = {int(r["element"]) for r in history if int(r["gw"]) < gw}
    return {i: values.get(i, NEW_PLAYER_PRIOR[int(row["element_type"])]) for i, row in catalog.items() if i in prior_ids or i not in prior_ids}


def run(history_path: pathlib.Path, catalog_dir: pathlib.Path, gw2_path: pathlib.Path, output: pathlib.Path, official_history_path: pathlib.Path | None = None) -> dict[str, Any]:
    history = load_jsonl(history_path)
    actual = {(int(r["gw"]), int(r["element"])): r for r in history}
    gw2 = json.loads(gw2_path.read_text())
    if gw2["entry_history"]["points"] != 99 or gw2["entry_history"]["total_points"] != 142:
        raise AssertionError("GW2 baseline reconciliation failed")
    if official_history_path is not None:
        official = json.loads(official_history_path.read_text())["current"]
        gw6 = next(row for row in official if row["event"] == 6)
        if gw6["total_points"] != 340:
            raise AssertionError(f"official GW6 baseline is {gw6['total_points']}, expected 340")
    initial = [int(p["element"]) for p in gw2["picks"]]
    results: dict[str, Any] = {}
    for model in ("two_stage_bayes", "ridge", "ep_next"):
        squad = initial[:]
        # Conservative starting-market sensitivity: wildcard budget is GW3 catalog
        # prices for the actual GW2 squad plus the recorded GW2 bank (£1.0m).
        first_raw = {int(r["id"]): r for r in json.loads((catalog_dir / "bootstrap_20260904_1615.json").read_text())["elements"]}
        first_catalog = active_catalog(catalog_dir / "bootstrap_20260904_1615.json")
        start_budget = 10 + sum(int(first_raw[i]["now_cost"]) for i in squad)
        holdings = {i: Holding(int(first_catalog[i]["now_cost"]), int(first_catalog[i]["now_cost"])) for i in squad if i in first_catalog}
        bank = 10
        rows = []
        cumulative = 142
        for gw in GWS:
            raw_catalog = {int(r["id"]): r for r in json.loads((catalog_dir / {3: "bootstrap_20260904_1615.json", 4: "bootstrap_20260912_1215.json", 5: "bootstrap_20260918_1615.json", 6: "bootstrap_20261010_0833.json"}[gw]).read_text())["elements"]}
            catalog = active_catalog(catalog_dir / {3: "bootstrap_20260904_1615.json", 4: "bootstrap_20260912_1215.json", 5: "bootstrap_20260918_1615.json", 6: "bootstrap_20261010_0833.json"}[gw])
            if gw > 3:
                for held_id in squad:
                    if held_id not in catalog:
                        retained = dict(raw_catalog[held_id])
                        retained["_can_play"] = False
                        catalog[held_id] = retained
            for row in catalog.values():
                row.setdefault("_can_play", True)
            if model == "ep_next":
                predictions = {i: float(row.get("ep_next") or 0.0) for i, row in catalog.items()}
            else:
                predictions = model_predictions(history, gw, catalog, model)
            if gw == 3:
                # Wildcard: full rebuild from actual GW2 sell-value budget; prices are
                # deliberately valued at GW3 catalog prices (documented sensitivity).
                squad, xi, bench, cap, vice, spent, bank = choose_squad(catalog, predictions, start_budget, None, None)
                holdings = {i: Holding(int(catalog[i]["now_cost"]), int(catalog[i]["now_cost"])) for i in squad}
                transfer_count = 15
            else:
                squad, xi, bench, cap, vice, spent, bank = choose_squad(catalog, predictions, bank, squad, holdings)
                transfer_count = 0 if set(squad) == set(holdings) else 1
                for i in squad:
                    if i not in holdings:
                        holdings[i] = Holding(int(catalog[i]["now_cost"]), int(catalog[i]["now_cost"]))
                holdings = {i: holdings[i] for i in squad}
            if not set(squad) <= set(catalog):
                raise AssertionError(f"GW{gw} blocked player selected")
            if bank < 0 or not legal_squad(squad, catalog) or not legal_xi(xi, catalog):
                raise AssertionError(f"GW{gw} legality/cash invariant failed: bank={bank} squad={legal_squad(squad, catalog)} xi={legal_xi(xi, catalog)}")
            positions = {i: int(catalog.get(i, first_raw.get(i))["element_type"]) for i in squad}
            result = score(xi, bench, cap, vice, gw, actual, positions)
            official_packet = json.loads((catalog_dir.parent.parent / "points-backtest" / f"entry_2797967_event_{gw}_picks_.json").read_text()) if False else None
            actual_team = None
            rows.append({"gw": gw, "points": result["total"], "xi": xi, "bench": bench, "captain": cap, "vice": vice, "transfer_count": transfer_count, "bank": bank, "squad_cost": spent, "substitutions": result["subs"], "effective_captain": result["effective_captain"]})
            cumulative += result["total"]
        results[model] = {"model": model, "starting_gw1_2": 142, "rows": rows, "cumulative_gw6": round(cumulative, 6), "delta_to_official_340": round(cumulative - 340, 6), "budget_sensitivity": "GW3 wildcard holdings valued at GW3 catalog prices because historical purchase ledger for all GW2 players is unavailable."}
    manifest = {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size} for p in sorted(catalog_dir.glob("bootstrap_*.json"))}
    out = {"history_sha256": hashlib.sha256(history_path.read_bytes()).hexdigest(), "catalog_manifest": manifest, "models": results, "policy": {"wildcard_gw": 3, "free_transfers": "at most one per GW4-6; no hits; no banked-FT exploit", "availability": "status a and chance_of_playing_next_round != 0", "new_player_prior": NEW_PLAYER_PRIOR, "unfinished_gw_excluded": 7}, "limits": {"capture_note": "Filenames and save timestamps are operational provenance, not cryptographic proof of deadline capture.", "history_max_gw": 6, "history_captured_at_max": max(r["captured_at"] for r in history)}}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=pathlib.Path, required=True)
    parser.add_argument("--catalog-dir", type=pathlib.Path, required=True)
    parser.add_argument("--gw2-picks", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--official-history", type=pathlib.Path)
    args = parser.parse_args()
    result = run(args.history, args.catalog_dir, args.gw2_picks, args.output, args.official_history)
    print(json.dumps({k: {"cumulative_gw6": v["cumulative_gw6"], "delta_to_official_340": v["delta_to_official_340"]} for k, v in result["models"].items()}, indent=2))


if __name__ == "__main__":
    main()
