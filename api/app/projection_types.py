"""Stable, ownership-independent contracts for the V5 laboratory model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


PROJECTION_VERSION = "projection-v5.0-lab"


@dataclass(frozen=True)
class ExpectedMinutes:
    p_start: float
    p_bench_appearance: float
    minutes_if_start: float
    minutes_if_bench: float
    expected_minutes: float
    p_60_plus: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class PlayerProjection:
    element: int
    name: str
    team: str
    position: str
    xpts_mean: float
    p10: float
    p50: float
    p90: float
    p_return: float
    p_10_plus: float
    expected_minutes: ExpectedMinutes
    components: dict[str, float]
    source: str
    quality_issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["expected_minutes"] = self.expected_minutes.to_dict()
        result["quality_issues"] = list(self.quality_issues)
        result["uncertainty"] = {
            "status": "heuristic_not_calibrated",
            "method": "deterministic_range_around_mean",
            "labels": {"p10": "low_range", "p50": "central_estimate", "p90": "high_range"},
        }
        return result
