"""Score only frozen pre-deadline predictions against finalized journal outcomes."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from app.backtest import pair_model_rows, score


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_hash(payload: dict, field: str) -> bool:
    expected = payload.get(field)
    body = dict(payload); body.pop(field, None)
    actual = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return bool(expected and expected == actual)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default="2026-27")
    parser.add_argument("--output")
    args = parser.parse_args()
    # "v5" is the frozen-journal wire key (the API's live projection, whatever
    # its internal version). Report it under a name that matches the engine /
    # bot, which call the live model "competitive-v4.x".
    PRODUCTION_KEY = "production_model"
    models = {PRODUCTION_KEY: [], "fpl_ep_next": []}
    evidence = []
    production_versions = set()
    journal_root = ROOT / "data" / "journal" / args.season
    raw_root = ROOT / "data" / "journal-raw" / args.season
    for outcome_path in sorted(journal_root.glob("gw*.json")):
        outcome = read(outcome_path)
        gameweek = int(outcome.get("gameweek") or 0)
        prediction_path = raw_root / f"gw{gameweek:02d}" / "predeadline.json"
        if not prediction_path.is_file():
            evidence.append({"gameweek": gameweek, "status": "missing_frozen_prediction"})
            continue
        prediction = read(prediction_path)
        captured = datetime.fromisoformat(prediction["captured_at"].replace("Z", "+00:00"))
        deadline = datetime.fromisoformat(prediction["deadline"].replace("Z", "+00:00"))
        valid = verify_hash(prediction, "input_hash") and verify_hash(outcome, "record_hash") and captured < deadline
        if not valid:
            evidence.append({"gameweek": gameweek, "status": "integrity_failure"})
            continue
        actual_rows = outcome.get("outcome", {}).get("squad", [])
        version = (prediction.get("source_provenance", {}).get("v5_model")
                   or prediction.get("v5", {}).get("projection_version"))
        if version:
            production_versions.add(str(version))
        models[PRODUCTION_KEY].extend(pair_model_rows(
            gameweek=gameweek, predictions=prediction.get("v5", {}).get("players", []),
            actual_rows=actual_rows, prediction_field="xpts_mean",
        ))
        models["fpl_ep_next"].extend(pair_model_rows(
            gameweek=gameweek, predictions=prediction.get("fpl_baseline", []),
            actual_rows=actual_rows, prediction_field="ep_next",
        ))
        evidence.append({"gameweek": gameweek, "status": "paired", "scope": "personal_squad",
                         "actual_rows": len(actual_rows)})
    result = {
        "schema_version": 1, "season": args.season,
        "method": "walk_forward_frozen_predeadline_to_final_personal_squad",
        "production_model_version": sorted(production_versions) or None,
        "evidence": evidence,
        "models": {name: score(rows) for name, rows in models.items()},
        "maturity": {
            "paired_gameweeks": sum(1 for row in evidence if row.get("status") == "paired"),
            "minimum_gameweeks_for_review": 6,
            "promotion_eligible": sum(1 for row in evidence if row.get("status") == "paired") >= 6,
            "status": "review_ready" if sum(1 for row in evidence if row.get("status") == "paired") >= 6 else "insufficient_evidence",
        },
        "limitations": [
            "Public journal outcomes cover the personal 15-player squad, not the full player universe.",
            "Production V4 and V4.2 lack row-level frozen predictions in the repository contract.",
            "Missing gameweeks are never reconstructed from later bootstrap data.",
        ],
    }
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

