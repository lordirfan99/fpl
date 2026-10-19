"""Production learned-v5 projection with a causal, cached two-stage fit.

Training joins archived pre-deadline catalog rows to finalized event outcomes.
GW1 is never used as a learned row because its aggregate is prior-season data.
The fit is built once per source fingerprint and every forecast is made from one
current-cutoff catalog/fixture snapshot; no future capture or outcome is read.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

VERSION = "learned-v5.0-two-stage-classifier-ridge"
FEATURES = (
    "ep_next", "form", "points_per_game", "value_form", "value_season",
    "selected_by_percent", "now_cost", "minutes", "starts", "total_points",
    "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_per_90", "expected_assists_per_90", "expected_goal_involvements_per_90",
    "influence", "creativity", "threat", "ict_index", "bps", "bonus",
    "clean_sheets", "goals_scored", "assists", "penalties_order",
)
POSITION_FLAGS = (1, 2, 3, 4)
MIN_TRAINING_ROWS = 40
FIT_CONFIG = {
    "classifier": {"n_estimators": 128, "max_depth": 5, "min_samples_leaf": 8,
                    "random_state": 0, "n_jobs": 1},
    "ridge_alpha": 10.0,
}


class LearnedModelUnavailable(RuntimeError):
    """Raised when learned activation cannot be proven safe."""


def _f(value: Any) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _row_features(row: dict[str, Any], exposure_gw: int) -> list[float]:
    gw = max(1, int(exposure_gw))
    minutes = _f(row.get("minutes"))
    points = _f(row.get("total_points"))
    xg = _f(row.get("expected_goals"))
    xa = _f(row.get("expected_assists"))
    vector = [_f(row.get(name)) for name in FEATURES]
    position = int(_f(row.get("element_type", row.get("position", 3))))
    vector.extend(float(position == value) for value in POSITION_FLAGS)
    vector.extend((points / gw, minutes / gw, xg / gw, xa / gw))
    exposure = max(minutes / 90.0, 1.0)
    vector.extend((points / exposure, xg / exposure, xa / exposure))
    return vector


def _state(row: dict[str, Any]) -> int:
    minutes = _f(row.get("minutes"))
    return 0 if minutes <= 0 else 1 if minutes < 60 else 2


def _history_context(history: list[dict[str, Any]], before_gw: int):
    by_player: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_team: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        if int(_f(row.get("gw"))) >= before_gw:
            continue
        by_player[int(_f(row.get("element")))].append(row)
        by_team[int(_f(row.get("team")))].append(row)
    return by_player, by_team


def _snapshot_file(directory: Path, gw: int) -> Path | None:
    for name in (f"predeadline_gw{gw}.json", f"bootstrap_gw{gw}.json",
                 f"gw{gw}.json", f"bootstrap_{gw}.json"):
        path = directory / name
        if path.is_file():
            return path
    return None


def load_predeadline_snapshots(directory: str | os.PathLike[str], max_gw: int) -> tuple[dict[int, dict[int, dict[str, Any]]], dict[str, str]]:
    """Load only explicitly named current-season snapshot files."""
    root = Path(directory)
    snapshots: dict[int, dict[int, dict[str, Any]]] = {}
    fingerprints: dict[str, str] = {}
    for gw in range(2, int(max_gw) + 1):
        path = _snapshot_file(root, gw)
        if path is None:
            continue
        raw = path.read_bytes()
        payload = json.loads(raw)
        rows = payload.get("elements", payload.get("players", payload))
        if not isinstance(rows, list):
            raise LearnedModelUnavailable(f"invalid pre-deadline snapshot shape: {path.name}")
        indexed = {int(row["id"]): dict(row) for row in rows if isinstance(row, dict) and row.get("id")}
        if len(indexed) != len(rows):
            raise LearnedModelUnavailable(f"duplicate/malformed players in {path.name}")
        snapshots[gw] = indexed
        fingerprints[path.name] = hashlib.sha256(raw).hexdigest()
    return snapshots, fingerprints


def _training(history: list[dict[str, Any]], snapshots: dict[int, dict[int, dict[str, Any]]], target_gw: int):
    actual = {(int(_f(row.get("gw"))), int(_f(row.get("element")))): row for row in history}
    xs: list[list[float]] = []
    ys: list[float] = []
    states: list[int] = []
    for gw in sorted(gw for gw in snapshots if 2 <= gw < int(target_gw)):
        for player, snapshot in sorted(snapshots[gw].items()):
            outcome = actual.get((gw, player))
            if outcome is None:
                continue
            xs.append(_row_features(snapshot, gw))
            ys.append(_f(outcome.get("total_points")))
            states.append(_state(outcome))
    if len(xs) < MIN_TRAINING_ROWS:
        raise LearnedModelUnavailable(
            f"learned-v5 requires >= {MIN_TRAINING_ROWS} prior finalized rows; found {len(xs)} before GW{target_gw}"
        )
    return xs, ys, states


def _fit(xs: list[list[float]], ys: list[float], states: list[int]):
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as error:
        raise LearnedModelUnavailable("learned-v5 dependencies unavailable: install pinned sklearn/joblib only") from error
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    state_array = np.asarray(states, dtype=int)
    classifier = make_pipeline(StandardScaler(), RandomForestClassifier(**FIT_CONFIG["classifier"]))
    classifier.fit(x, state_array)
    regressors: dict[int, Any] = {}
    means: dict[int, float] = {}
    for state in range(3):
        mask = state_array == state
        means[state] = float(np.mean(y[mask])) if mask.any() else (0.0 if state == 0 else 2.0)
        if int(mask.sum()) >= 2:
            model = make_pipeline(StandardScaler(), Ridge(alpha=FIT_CONFIG["ridge_alpha"]))
            model.fit(x[mask], y[mask])
            regressors[state] = model
    residuals = []
    for index, row in enumerate(x):
        probability = classifier.predict_proba([row])[0]
        classes = {int(value): offset for offset, value in enumerate(classifier[-1].classes_)}
        prediction = sum(probability[classes[state]] * float(regressors[state].predict([row])[0])
                         if state in regressors else probability[classes[state]] * means[state]
                         for state in range(3) if state in classes)
        residuals.append(float(y[index] - prediction))
    return classifier, regressors, means, max(0.75, float(np.std(residuals)))


def _fixture_adjustment(fixtures: list[dict[str, Any]], team: int, gw: int) -> tuple[float, int, float]:
    rows = [row for row in fixtures if int(row.get("event") or 0) == int(gw)
            and int(team) in (int(row.get("team_h") or 0), int(row.get("team_a") or 0))]
    fdrs = []
    for row in rows:
        home = int(row.get("team_h") or 0) == int(team)
        fdrs.append(_f(row.get("team_h_difficulty" if home else "team_a_difficulty")))
    if not fdrs:
        return 1.0, 0, 0.0
    mean_fdr = sum(fdrs) / len(fdrs)
    # Fixed, bounded FDR adjustment: future fixture metadata only; supports doubles.
    adjustment = max(0.91, min(1.09, 1.0 + (3.0 - mean_fdr) * 0.03))
    return adjustment, len(fdrs), mean_fdr


def _fingerprint(history: list[dict[str, Any]], current: list[dict[str, Any]], snapshots_fp: dict[str, str], fixtures: list[dict[str, Any]], target_gws: list[int]) -> str:
    payload = {"history": history, "current": current, "snapshots": snapshots_fp,
               "fixtures": fixtures, "target_gws": target_gws, "config": FIT_CONFIG, "version": VERSION}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def predict_catalog(history: list[dict[str, Any]], current_elements: list[dict[str, Any]], target_gws: list[int], fixtures: list[dict[str, Any]], snapshot_dir: str | os.PathLike[str]) -> dict[int, dict[str, Any]]:
    """Fit once, then score the whole catalog for all frozen future GWs."""
    if not target_gws:
        raise LearnedModelUnavailable("learned-v5 received no target gameweeks")
    max_prior = max(2, int(target_gws[0]))
    snapshots, snapshot_fps = load_predeadline_snapshots(snapshot_dir, max_prior)
    xs, ys, states = _training(history, snapshots, int(target_gws[0]))
    cache_key = _fingerprint(history, current_elements, snapshot_fps, fixtures, target_gws)
    cached = getattr(predict_catalog, "_cache", None)
    if cached and cached[0] == cache_key:
        fitted, training_count, residual_sd = cached[1:]
    else:
        fitted = _fit(xs, ys, states)
        training_count, residual_sd = len(ys), fitted[-1]
        predict_catalog._cache = (cache_key, fitted, training_count, residual_sd)
    classifier, regressors, means, residual_sd = fitted
    import numpy as np
    outputs: dict[int, dict[str, Any]] = {}
    for element in current_elements:
        player_id = int(element["id"])
        vector = np.asarray([_row_features(element, max_prior)], dtype=float)
        probabilities = classifier.predict_proba(vector)[0]
        classes = {int(value): offset for offset, value in enumerate(classifier[-1].classes_)}
        conditional = {state: (float(regressors[state].predict(vector)[0]) if state in regressors else means[state]) for state in range(3)}
        base = max(0.0, sum(probabilities[classes[state]] * conditional[state] for state in range(3) if state in classes))
        p_dnp = probabilities[classes[0]] if 0 in classes else 0.0
        p_cameo = probabilities[classes[1]] if 1 in classes else 0.0
        p_starter = probabilities[classes[2]] if 2 in classes else 0.0
        per_gw = []
        fixture_meta = []
        for gw in target_gws:
            adjustment, count, fdr = _fixture_adjustment(fixtures, int(element.get("team") or 0), int(gw))
            per_gw.append(round(base * adjustment, 3))
            fixture_meta.append({"gw": int(gw), "fixture_count": count, "fdr": round(fdr, 3), "adjustment": round(adjustment, 4)})
        mean = per_gw[0]
        variance = max(0.35, residual_sd ** 2 + mean * (1.0 - p_starter) * 1.5)
        outputs[player_id] = {
            "xpts": mean, "xpts_by_gw": per_gw,
            "expected_horizon": round(sum(weight * value for weight, value in zip((1.0, 0.7, 0.5), per_gw)), 3),
            "xpts_horizon": round(sum(weight * value for weight, value in zip((1.0, 0.7, 0.5), per_gw)), 3),
            "risk_adjusted_xpts": round(max(0.0, mean - 0.25 * math.sqrt(variance)), 3),
            "xpts_variance": round(variance, 3), "variance_by_gw": [round(variance, 3)] * len(per_gw),
            "p_dnp": round(p_dnp, 4), "p_1_59": round(p_cameo, 4), "p_60_plus": round(p_starter, 4),
            "p_start": round(p_starter, 4), "expected_minutes": round(59.0 * p_cameo + 75.0 * p_starter, 2),
            "confidence": None,
            "uncertainty_multiplier": round(residual_sd, 3),
            "model_version": VERSION, "training_rows": training_count,
            "training_gameweeks": sorted(gw for gw in snapshots if 2 <= gw < int(target_gws[0])),
            "fixture_features": fixture_meta,
            "degraded_reasons": ["confidence_not_reported; residual uncertainty exposed instead"],
            "components": {"learned_two_stage": round(mean, 3)},
        }
    return outputs


def activation(settings: dict[str, Any], registry_path: str | os.PathLike[str] | None = None) -> tuple[bool, str]:
    """Require an explicit learned-v5 setting or registry entry."""
    config = settings.get("learned_model") or {}
    if config.get("enabled") is True and config.get("version") == VERSION:
        return True, "settings"
    if registry_path and os.path.isfile(registry_path):
        try:
            registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
            if registry.get("active_projection") == VERSION:
                return True, "registry"
        except (OSError, ValueError, TypeError):
            pass
    return False, "explicit learned-v5 activation missing"


def reset_cache() -> None:
    predict_catalog._cache = None
