#!/usr/bin/env python3
"""Run the prespecified offline model tournament on cached finalized data."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve()
ENGINE = HERE.parents[1]
sys.path.insert(0, str(ENGINE / "model"))
from competitive_two_stage_candidate import CONFIGS, predict_gameweek  # noqa: E402
from competitive_v44_candidate import predict_gameweek as predict_ridge  # noqa: E402


def load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    seen = set()
    for row in rows:
        key = (int(row["gw"]), int(row["element"]))
        if key in seen:
            raise ValueError(f"duplicate history row {key}")
        seen.add(key)
    return rows


def legal(ids: list[int], positions: dict[int, int]) -> bool:
    counts = {position: sum(positions[item] == position for item in ids) for position in (1, 2, 3, 4)}
    return len(ids) == 11 and counts[1] == 1 and counts[2] >= 3 and counts[3] >= 2 and counts[4] >= 1


def score(starters: list[int], bench: list[int], captain: int, vice: int, gw: int, positions: dict[int, int], chip: str | None, actual: dict[tuple[int, int], dict[str, Any]]) -> dict[str, Any]:
    minutes = lambda item: float(actual.get((gw, item), {}).get("minutes", 0))
    points = lambda item: float(actual.get((gw, item), {}).get("total_points", 0))
    final = set(starters)
    subs: list[list[int]] = []
    if chip != "bboost":
        missing = [item for item in starters if minutes(item) == 0]
        available = [item for item in bench if minutes(item) > 0]
        candidates = []
        for number in range(min(len(missing), len(available)) + 1):
            for incoming in itertools.combinations(available, number):
                for outgoing in itertools.combinations(missing, number):
                    ids = (set(starters) - set(outgoing)) | set(incoming)
                    if sum(positions[item] == 1 for item in incoming) == sum(positions[item] == 1 for item in outgoing) and legal(list(ids), positions):
                        priority = tuple(int(item in incoming) for item in bench)
                        candidates.append((number, priority, ids, list(zip(outgoing, incoming))))
        if not candidates:
            raise AssertionError(f"no legal auto-substitution candidate GW{gw}")
        _, _, final, subs = max(candidates, key=lambda value: (value[0], value[1]))
    base = sum(points(item) for item in (starters + bench if chip == "bboost" else final))
    effective = captain if minutes(captain) > 0 else vice if minutes(vice) > 0 else None
    extra = points(effective) * (2 if chip == "3xc" else 1) if effective else 0.0
    return {"total": base + extra, "base": base, "captain_extra": extra, "effective_captain": effective, "subs": subs}


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    errors = [row["predicted"] - row["actual"] for row in rows]
    return {"n": len(rows), "mae": round(sum(abs(error) for error in errors) / len(errors), 6), "rmse": round((sum(error * error for error in errors) / len(errors)) ** 0.5, 6), "bias": round(sum(errors) / len(errors), 6)}


def run(history_path: pathlib.Path, cache_dir: pathlib.Path, output_dir: pathlib.Path, manifest_path: pathlib.Path) -> dict[str, Any]:
    history = load_jsonl(history_path)
    actual = {(int(row["gw"]), int(row["element"])): row for row in history}
    picks = {gw: json.loads((cache_dir / f"entry_2797967_event_{gw}_picks_.json").read_text()) for gw in range(3, 7)}
    bootstrap = json.loads((cache_dir / "bootstrap-static_.json").read_text())
    names = {int(row["id"]): row["web_name"] for row in bootstrap["elements"]}
    all_rows: dict[str, list[dict[str, Any]]] = {config.name: [] for config in CONFIGS}
    per_model: dict[str, list[dict[str, Any]]] = {config.name: [] for config in CONFIGS}
    for gw in range(3, 7):
        packet = picks[gw]
        ordered = sorted(packet["picks"], key=lambda item: item["position"])
        squad = [int(item["element"]) for item in ordered]
        prior = {int(row["element"]): row for row in history if int(row["gw"]) < gw}
        if set(squad) - set(prior):
            raise ValueError(f"GW{gw} owned player without prior metadata")
        positions = {item: int(prior[item]["position"]) for item in squad}
        ridge = {row["element"]: row["predicted"] for row in predict_ridge(history, gw)}
        for config in CONFIGS:
            predictions = {row["element"]: row["predicted"] for row in predict_gameweek(history, gw, config, ridge)}
            missing = set(squad) - set(predictions)
            if missing:
                raise ValueError(f"{config.name} missing owned forecast GW{gw}: {sorted(missing)}")
            lineup = max((list(combo) for combo in itertools.combinations(squad, 11) if legal(list(combo), positions)), key=lambda combo: (sum(predictions[item] for item in combo) + max(predictions[item] for item in combo), tuple(sorted(combo))))
            ranked = sorted(lineup, key=lambda item: (-predictions[item], item))
            captain, vice = ranked[:2]
            bench = sorted(set(squad) - set(lineup), key=lambda item: (positions[item] != 1, -predictions[item], item))
            result = score(lineup, bench, captain, vice, gw, positions, packet.get("active_chip"), actual)
            official = sum(float(actual[(gw, item["element"])]["total_points"]) * item["multiplier"] for item in ordered)
            recorded = next(row for row in json.loads((cache_dir / "entry_2797967_history_.json").read_text())["current"] if row["event"] == gw)
            if abs(official - (recorded["points"])) > 1e-9:
                raise AssertionError(f"official reconciliation failed GW{gw}")
            hit = float(recorded.get("event_transfers_cost", 0))
            row = {"gw": gw, "predicted_team": round(result["total"] - hit, 6), "actual_team": official - hit, "gain": round(result["total"] - official, 6), "captain": names.get(captain, str(captain)), "vice": names.get(vice, str(vice)), "captain_id": captain, "vice_id": vice, "starters": [names.get(item, str(item)) for item in lineup], "bench": [names.get(item, str(item)) for item in bench], "chip": packet.get("active_chip"), "subs": result["subs"]}
            per_model[config.name].append(row)
            for item, prediction in predictions.items():
                key = (gw, item)
                if key in actual:
                    all_rows[config.name].append({"predicted": prediction, "actual": float(actual[key]["total_points"]), "gw": gw, "element": item})
    results = {}
    for config in CONFIGS:
        rows = per_model[config.name]
        results[config.name] = {"model": config.name, "forecast_metrics": {str(gw): metrics([row for row in all_rows[config.name] if row["gw"] == gw]) for gw in range(3, 7)}, "decision_rows": rows, "totals": {"candidate": round(sum(row["predicted_team"] - next(r["actual_team"] for r in rows if r["gw"] == row["gw"]) + next(r["actual_team"] for r in rows if r["gw"] == row["gw"]) for row in rows), 6), "actual": round(sum(row["actual_team"] for row in rows), 6), "gain": round(sum(row["gain"] for row in rows), 6)}}
    output = {"manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(), "history_sha256": hashlib.sha256(history_path.read_bytes()).hexdigest(), "models": results, "oracle_ceiling": {"actual": 198.0, "perfect_hindsight": 264.0, "maximum_extra": 66.0}, "caveat": "Retrospective model-development research on six finalized GWs; no holdout claim and no transfer-policy claim."}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "tournament.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", required=True, type=pathlib.Path)
    parser.add_argument("--cache-dir", required=True, type=pathlib.Path)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    args = parser.parse_args()
    result = run(args.history, args.cache_dir, args.output_dir, args.manifest)
    print(json.dumps({name: {"gain": model["totals"]["gain"], "rows": model["decision_rows"]} for name, model in result["models"].items()}, indent=2))


if __name__ == "__main__":
    main()
