"""Bounded synthetic production monitor with payload budgets."""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

API = os.getenv("FPL_API_BASE_URL", "https://fpl-scout-api-bztsnhv3ea-uc.a.run.app").rstrip("/")
SITE = os.getenv("FPL_SITE_URL", "https://fpl-scout-intelligence.netlify.app").rstrip("/")


def fetch(url: str, limit: int) -> tuple[int, bytes, dict[str, str]]:
    request = Request(url, headers={"User-Agent": "FPLScoutMonitor/1.0"})
    with urlopen(request, timeout=60) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise RuntimeError(f"payload budget exceeded: {url} > {limit} bytes")
        return response.status, body, dict(response.headers)


def validate_recommendation(rec: dict) -> None:
    fresh = rec.get("freshness") or {}
    meta = rec.get("meta") or {}
    packet = rec.get("packet_status")
    if packet in ("safe_hold", "needs_refresh"):
        if rec.get("transfers") or rec.get("captains"):
            raise RuntimeError("Held recommendation still contains personal actions")
        return
    if (packet != "advisory" or fresh.get("status") not in ("fresh", "provisional")
            or fresh.get("stale") is not False or meta.get("stale") is not False):
        raise RuntimeError(f"Recommendation freshness is stale or unknown: {fresh}")
    if fresh.get("account_state_verified") is not True and (rec.get("transfers") or rec.get("captains")):
        raise RuntimeError("Unverified current account still has personal recommendations")


def main() -> int:
    status, body, _ = fetch(f"{API}/ready", 50_000)
    readiness = json.loads(body)
    assert status == 200 and readiness["ready"] is True, readiness
    # Unlike /ready, this checks the background collector's actual output.
    for league_id in (58005, 131997):
        status, body, _ = fetch(f"{API}/v1/leagues/{league_id}/live/status", 50_000)
        live_status = json.loads(body)
        if status != 200 or live_status.get("ready") is not True:
            raise RuntimeError(f"Live league {league_id} snapshot is stale or unavailable: {live_status}")

    # The recommendation packet must not silently serve old transfers. A
    # `safe_hold` / `needs_refresh` is an ACCEPTED honest degradation; a `stale`
    # or plainly `stale=true` advisory packet is a monitoring failure.
    for league_id in (58005, 131997):
        for route in ("recommendations", "decision"):
            status, body, _ = fetch(f"{API}/v1/{route}/current?league_id={league_id}", 250_000)
            rec = json.loads(body)
            assert status == 200, rec
            validate_recommendation(rec)
            fresh = rec.get("freshness") or {}
            print(f"{route} L{league_id}: status={fresh.get('status')} packet={rec.get('packet_status')} age_h={fresh.get('data_age_hours')}")

    status, body, headers = fetch(f"{API}/v1/leagues/58005/summary?page=1&page_size=50", 250_000)
    summary = json.loads(body)
    assert status == 200 and len(summary["managers"]) <= 50
    assert all("squad" not in manager for manager in summary["managers"])
    assert headers.get("server-timing"), headers
    fetch(f"{API}/v1/catalog/compact", 350_000)
    fetch(f"{SITE}/league", 1_500_000)
    fetch(f"{SITE}/compare", 1_500_000)
    fetch(f"{SITE}/journal", 1_500_000)
    print("Production readiness, contracts and payload budgets passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
