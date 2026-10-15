#!/usr/bin/env python3
"""Score immutable pre-deadline captures against a post-GW actuals file.

The actuals input is intentionally explicit: this job never fetches live data
and never changes a forecast capture. Example:
  python jobs/evaluate_frozen_forecasts.py --gw 7 --actuals /path/actuals.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import tempfile

from project_paths import resolve_project_root
from evaluation_capture import evaluate_capture, validate_provenance

BASE = str(resolve_project_root(__file__))
PROCESSED = os.path.join(BASE, "data", "processed")


def _write_once(path: str, value: dict) -> bool:
    if os.path.exists(path):
        return False
    fd, tmp = tempfile.mkstemp(prefix="evaluation-", suffix=".tmp", dir=os.path.dirname(path), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gw", type=int, required=True)
    parser.add_argument("--actuals", required=True, help="JSON object mapping player id to points")
    args = parser.parse_args()
    with open(args.actuals, encoding="utf-8") as handle:
        actuals = {int(key): float(value) for key, value in json.load(handle).items()}
    plan_path = os.path.join(PROCESSED, f"plan_gw{args.gw}.json")
    plan = None
    if os.path.exists(plan_path):
        with open(plan_path, encoding="utf-8") as handle:
            plan = json.load(handle)
    captures = sorted(glob.glob(os.path.join(PROCESSED, f"forecast_capture_gw{args.gw}_*.json")))
    if not captures:
        raise SystemExit(f"no immutable forecast captures for GW{args.gw}")
    written = 0
    for capture_path in captures:
        with open(capture_path, encoding="utf-8") as handle:
            capture = json.load(handle)
        validate_provenance(capture)
        report = evaluate_capture(capture, actuals, plan=plan)
        stem = os.path.basename(capture_path).removeprefix("forecast_capture_").removesuffix(".json")
        output = os.path.join(PROCESSED, f"evaluation_{stem}.json")
        written += int(_write_once(output, report))
        print(f"{capture['model_version']} {capture['projection_features']}: {output}")
    print(f"evaluation reports created: {written}; existing reports preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
