"""Leakage-safe frozen forecast capture and decision evaluation.

A forecast is evidence only when its provenance proves that every feature snapshot
was available strictly before the gameweek deadline. Captures are immutable by
(gameweek, model version, feature version); later runs may read them but cannot
replace them. Actuals are joined only by the post-deadline evaluator.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import tempfile
from typing import Any

SCHEMA_VERSION = 1
UTC = dt.timezone.utc


def _time(value: Any) -> dt.datetime:
    if not value or not isinstance(value, str):
        raise ValueError("missing timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(UTC)


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_") or "unknown"


def capture_path(directory: str, gw: int, model_version: str, feature_version: str) -> str:
    """Return a model/feature-separated canonical capture path."""
    return os.path.join(directory, f"forecast_capture_gw{int(gw)}_"
                         f"{_safe_name(model_version)}_{_safe_name(feature_version)}.json")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                      default=str).encode("utf-8")).hexdigest()


def validate_provenance(capture: dict[str, Any]) -> None:
    """Fail closed on missing, post-deadline, or future-dated provenance."""
    deadline = _time(capture.get("deadline"))
    generated = _time(capture.get("generated_at"))
    feature_as_of = _time(capture.get("feature_as_of"))
    if generated >= deadline or feature_as_of >= deadline:
        raise ValueError("forecast provenance is not strictly pre-deadline")
    if feature_as_of > generated:
        raise ValueError("feature_as_of is later than generated_at")
    if capture.get("actuals_attached"):
        raise ValueError("forecast capture cannot contain post-deadline actuals")
    if not capture.get("model_version") or not capture.get("projection_features"):
        raise ValueError("model_version and projection_features are required")
    if not capture.get("source_fingerprint"):
        raise ValueError("source_fingerprint is required")
    capture["provenance_valid"] = True


def freeze_capture(path: str, capture: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Create a capture once; return (stored value, created).

    Existing canonical evidence is never overwritten, even when the new payload
    has the same key and a later timestamp. This makes same-GW reruns auditable.
    """
    candidate = dict(capture)
    candidate["schema_version"] = SCHEMA_VERSION
    validate_provenance(candidate)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
        validate_provenance(stored)
        return stored, False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="forecast-capture-", suffix=".tmp",
                                dir=os.path.dirname(path), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(candidate, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return candidate, True


def baseline_rows(players: list[dict[str, Any]], position_baselines: dict[str, Any]) -> list[dict[str, Any]]:
    """Build same-time baselines without using actuals or future rows."""
    output = []
    for player in players:
        position = str(player.get("pos") or player.get("position") or "MID")
        minutes = _number(player.get("expected_minutes"), 0.0) or 0.0
        prior = _number(position_baselines.get(position), 0.0) or 0.0
        output.append({
            "id": int(player["id"]),
            "official_ep_next": _number(player.get("ep_next")),
            "lagged_position": round(max(0.0, prior) * max(0.0, minutes) / 90.0, 3),
        })
    return output


def build_capture(*, gw: int, deadline: str, generated_at: str, feature_as_of: str,
                  model_version: str, feature_version: str, source_payload: Any,
                  players: list[dict[str, Any]], position_baselines: dict[str, Any],
                  source_kind: str = "official-fpl") -> dict[str, Any]:
    """Normalize a forecast packet and attach immutable provenance."""
    base = {row["id"]: row for row in baseline_rows(players, position_baselines)}
    rows = []
    for player in players:
        row = dict(base[int(player["id"])])
        row.update({
            "name": player.get("name"),
            "pos": player.get("pos") or player.get("position"),
            "predicted": _number(player.get("xpts"), 0.0),
            "expected_minutes": _number(player.get("expected_minutes"), 0.0),
            "p_60_plus": _number(player.get("p_60_plus")),
        })
        rows.append(row)
    capture = {
        "schema_version": SCHEMA_VERSION, "artifact_type": "immutable_forecast_capture",
        "gw": int(gw), "deadline": deadline, "generated_at": generated_at,
        "feature_as_of": feature_as_of, "model_version": model_version,
        "projection_features": feature_version, "source_kind": source_kind,
        "source_fingerprint": _fingerprint(source_payload), "actuals_attached": False,
        "baseline_versions": {"official_ep_next": "fpl-bootstrap-ep_next",
                               "lagged_position": "prior-season-position-baseline"},
        "rows": rows,
    }
    validate_provenance(capture)
    return capture


def _metrics(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    if not pairs:
        return {"n": 0, "mae": None, "bias": None}
    errors = [pred - actual for pred, actual in pairs]
    return {"n": len(pairs), "mae": round(sum(abs(x) for x in errors) / len(errors), 4),
            "bias": round(sum(errors) / len(errors), 4)}


def _score_model(rows: list[dict[str, Any]], actuals: dict[int, float], field: str) -> dict[str, Any]:
    pairs = []
    decision = []
    for row in rows:
        pid = int(row["id"])
        if pid not in actuals or row.get(field) is None:
            continue
        pred, actual = float(row[field]), float(actuals[pid])
        pairs.append((pred, actual))
        if pred >= 4.0 and float(row.get("expected_minutes") or 0.0) >= 60.0:
            decision.append((pred, actual))
    return {"all": _metrics(pairs), "prospective_decision_cohort": _metrics(decision)}


def decision_metrics(plan: dict[str, Any], actuals: dict[int, float], *, executed: dict[str, Any] | None = None) -> dict[str, Any]:
    """Score recommendation semantics and keep executed state distinct."""
    starters = [int(p["id"]) for p in plan.get("target_starters", [])]
    bench = [int(p["id"]) for p in plan.get("bench", [])]
    captain = int((plan.get("captain") or {}).get("id") or 0)
    vice = int((plan.get("vice") or {}).get("id") or 0)
    starter_points = sum(float(actuals.get(pid, 0)) for pid in starters)
    captain_points = float(actuals.get(captain, 0))
    best = max((float(actuals.get(pid, 0)) for pid in starters), default=0.0)
    transfer_rows = []
    for transfer in plan.get("transfers", []):
        incoming, outgoing = int(transfer["element_in"]), int(transfer["element_out"])
        hit = 4 if transfer.get("hit") else 0
        transfer_rows.append({"in": incoming, "out": outgoing,
                              "net": round(float(actuals.get(incoming, 0)) - float(actuals.get(outgoing, 0)) - hit, 3)})
    result = {
        "recommended": {"xi_points": round(starter_points + captain_points, 3),
                         "bench_points": round(sum(float(actuals.get(pid, 0)) for pid in bench), 3),
                         "captain_id": captain, "vice_id": vice,
                         "captain_delta_to_best": round(captain_points - best, 3),
                         "transfers": transfer_rows},
        "executed": None,
        "executed_state": "unavailable",
    }
    if executed:
        result["executed"] = executed
        result["executed_state"] = "recorded"
    return result


def evaluate_capture(capture: dict[str, Any], actuals: dict[int, float], plan: dict[str, Any] | None = None,
                     executed: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_provenance(capture)
    rows = capture.get("rows") or []
    report = {"gw": capture["gw"], "model_version": capture["model_version"],
              "projection_features": capture["projection_features"],
              "source_fingerprint": capture["source_fingerprint"],
              "forecast": {"champion": _score_model(rows, actuals, "predicted"),
                           "official_ep_next": _score_model(rows, actuals, "official_ep_next"),
                           "lagged_position": _score_model(rows, actuals, "lagged_position")}}
    if plan is not None:
        report["decision"] = decision_metrics(plan, actuals, executed=executed)
    return report
