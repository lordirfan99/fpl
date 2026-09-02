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
