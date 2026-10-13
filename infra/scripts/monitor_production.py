"""Bounded synthetic production monitor with payload budgets."""
from __future__ import annotations

import argparse
import json
import os
from urllib.request import Request, urlopen

API = os.getenv("FPL_API_BASE_URL", "https://sportmania.duckdns.org/fpl-scout-api").rstrip("/")
SITE = os.getenv("FPL_SITE_URL", "https://fpl-scout-intelligence.netlify.app").rstrip("/")


def fetch(url: str, limit: int) -> tuple[int, bytes, dict[str, str]]:
    request = Request(url, headers={"User-Agent": "FPLScoutMonitor/1.0"})
    with urlopen(request, timeout=60) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise RuntimeError(f"payload budget exceeded: {url} > {limit} bytes")
        return response.status, body, dict(response.headers)



def check_page(path: str, expected_text: str, limit: int = 750_000) -> None:
    # A 200 status alone is not a working App Router page; error boundaries
    # and redirect shells can also be HTTP 200. Check useful SSR content.
    status, body, _ = fetch(f"{SITE}{path}", limit)
    html = body.decode("utf-8", errors="replace")
    if status != 200 or expected_text not in html or "We could not load this view." in html:
        raise RuntimeError(f"Dashboard route {path} did not render its expected content")


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


def main(*, lightweight: bool = False) -> int:
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
    normalized_headers = {key.casefold(): value for key, value in headers.items()}
    assert normalized_headers.get("server-timing"), headers
    fetch(f"{API}/v1/catalog/compact", 350_000)
    if lightweight:
        # Three cheap navigation destinations catch broken web deployments
        # without repeatedly hydrating multi-megabyte league snapshots.
        check_page("/sign-in", "Your private decision room")
        check_page("/this-week", "This gameweek")
        check_page("/settings", "Your dashboard preferences")
    else:
        check_page("/league", "League table", 1_500_000)
        check_page("/compare", "Compare managers", 1_500_000)
        check_page("/journal", "Decision Journal", 1_500_000)
    print("Production readiness, contracts and payload budgets passed")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lightweight", action="store_true", help="Skip data-heavy SSR page checks")
    raise SystemExit(main(lightweight=parser.parse_args().lightweight))
