"""Read the canonical competitive V4 decision context from the Scout API."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_API = "https://fpl-scout-api-bztsnhv3ea-uc.a.run.app"


class CompetitiveV4Error(RuntimeError):
    """The canonical V4 packet could not be read or was not trustworthy."""


def fetch_competitive_v4(league_id: int, gameweek: int, *, timeout: int = 30,
                         require_executable_plan: bool = True) -> dict[str, Any]:
    """Return a compact, validated V4 context for the current decision.

    Planning may consume a valid competitive snapshot before a newly bound
    plan exists. Execution callers retain the stricter default and require the
    API packet to contain an executable, fully bound plan.
    """
    base = os.getenv("FPL_COMPETITIVE_API_URL", DEFAULT_API).rstrip("/")
    query = urllib.parse.urlencode({"league_id": league_id, "gw": gameweek})
    request = urllib.request.Request(
        f"{base}/v1/decision/current?{query}",
        headers={"User-Agent": "fpl-autopilot/competitive-v4"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except Exception as error:  # pragma: no cover - network-specific detail
        raise CompetitiveV4Error(f"V4 API unavailable: {error!r}") from error

    competitive = payload.get("competitive") or {}
    model_version = competitive.get("model_version")
    if model_version != "competitive-v4.0":
        raise CompetitiveV4Error(f"unexpected V4 model version: {model_version!r}")
    meta = payload.get("meta") or {}
    if meta.get("quality_status") != "valid":
        raise CompetitiveV4Error(f"V4 snapshot quality is {meta.get('quality_status')!r}")
    packet_status = payload.get("packet_status")
    if require_executable_plan:
        if packet_status not in ("valid", "applied"):
            raise CompetitiveV4Error("V4 packet is safe_hold (no complete executable plan)")
        if packet_status == "valid" and not payload.get("executable"):
            raise CompetitiveV4Error("V4 pending packet is not executable")
        if packet_status == "applied" and payload.get("executable"):
            raise CompetitiveV4Error("V4 applied packet cannot remain executable")
        if not isinstance(payload.get("plan"), dict):
            raise CompetitiveV4Error("V4 packet has no complete plan")
    else:
        # A safe-hold OR advisory packet may still carry a fresh, valid league
        # model. It must never be treated as executable, but it is the correct
        # input for constructing the plan. "advisory" is what the API returns
        # for a finalized-GW decision packet (competitor-aware, not executable).
        if packet_status not in ("safe_hold", "advisory", "valid", "applied"):
            raise CompetitiveV4Error(f"unexpected V4 packet status: {packet_status!r}")
        if competitive.get("phase") is None or competitive.get("alignment") is None:
            raise CompetitiveV4Error("V4 competitive context is incomplete")

    def compact_players(key: str) -> list[dict[str, Any]]:
        rows = competitive.get(key) or []
        fields = ("element", "name", "position", "team", "cost", "xpts", "elite_ownership", "elite_captaincy", "fixture", "fdr", "risk", "role", "score", "count", "percentage", "elite_percentage", "starter_percentage")
        return [{field: row.get(field) for field in fields if field in row} for row in rows if isinstance(row, dict)]

    return {
        "model_version": model_version,
        "context_status": "ready",
        "phase": competitive.get("phase"),
        "phase_reason": competitive.get("phase_reason"),
        "alignment": competitive.get("alignment"),
        "target_alignment": competitive.get("target_alignment"),
        "core_owned": competitive.get("core_owned"),
        "core_size": competitive.get("core_size"),
        "phase_inputs": competitive.get("phase_inputs") or {},
        "critical_missing": compact_players("critical_missing"),
        "model_edges": compact_players("model_edges"),
        "disagreements": compact_players("disagreements"),
        "elite_template": compact_players("elite_template"),
        "template_formation": competitive.get("template_formation"),
        "captain_consensus": compact_players("captain_consensus"),
        "transfer_consensus": competitive.get("transfer_consensus") or [],
        "template_gate": competitive.get("template_gate") or {},
        "elite_count": payload.get("elite_count"),
        "elite_overlap": payload.get("elite_overlap"),
        "elite_average_points": payload.get("elite_average_points"),
        "meta": {key: meta.get(key) for key in ("run_id", "snapshot_at", "generated_at", "stale", "freshness_hours", "quality_status", "snapshot_gameweek")},
        "decision_id": payload.get("decision_id"),
        "packet_status": packet_status,
        "context_only": not require_executable_plan,
        "plan": payload.get("plan"),
    }
