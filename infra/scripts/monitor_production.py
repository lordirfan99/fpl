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
    status, body, _ = fetch(f"{API}/v1/recommendations/current?league_id=58005", 250_000)
    rec = json.loads(body)
    assert status == 200, rec
    fresh = rec.get("freshness") or {}
    rec_status = fresh.get("status") or (rec.get("meta") or {}).get("freshness_status")
    packet = rec.get("packet_status")
    if rec_status in ("stale", "needs_refresh") and packet != "safe_hold":
        raise RuntimeError(f"Recommendation is silently stale: status={rec_status} packet={packet} freshness={fresh}")
    if (rec.get("meta") or {}).get("stale") is True and packet not in ("safe_hold", "needs_refresh"):
        raise RuntimeError(f"Recommendation meta.stale but packet={packet}: {fresh}")
    print(f"Recommendation freshness ok: source={fresh.get('source')} status={rec_status} packet={packet} age_h={fresh.get('data_age_hours')}")

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
