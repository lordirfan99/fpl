"""Bounded synthetic production monitor with payload budgets."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from urllib.request import Request, urlopen

API = os.getenv("FPL_API_BASE_URL", "https://sportmania.duckdns.org/fpl-scout-api").rstrip("/")
SITE = os.getenv("FPL_SITE_URL", "https://fpl-scout-intelligence.netlify.app").rstrip("/")

# Inside this many hours of the next deadline, a live advisory recommendation
# must already be built for that gameweek (or be an honest hold). A packet still
# pointing at the previous gameweek that close to lock is a monitoring failure,
# not a healthy state.
PLAN_CURRENCY_WINDOW_H = 24.0


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
    # A `fresh` packet asserts the VM collected official per-manager entry_history
    # (real overall_rank + bank). If it silently fell back to classic-league rank,
    # the elite cohort and every template / differential / captain-consensus
    # signal built on it is quietly degraded — surface it instead of passing.
    if fresh.get("status") == "fresh":
        provenance = fresh.get("rank_provenance")
        if provenance not in (None, "official-entry-history"):
            raise RuntimeError(f"Elite cohort quality degraded: rank_provenance={provenance!r}")
        if fresh.get("bank_known") is False:
            raise RuntimeError("Fresh recommendation reports bank_known=false; live inputs incomplete")


def next_deadline(events: list[dict], *, now: dt.datetime) -> tuple[int, dt.datetime] | None:
    """(gameweek, deadline) for the first upcoming, unfinished event, else None."""
    upcoming: list[tuple[int, dt.datetime]] = []
    for event in events or []:
        raw = event.get("deadline_time")
        if not raw or event.get("finished"):
            continue
        try:
            when = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        if when > now or event.get("is_next") or event.get("is_current"):
            upcoming.append((int(event.get("id") or 0), when))
    if not upcoming:
        return None
    return min(upcoming, key=lambda row: row[1])


def check_plan_currency(rec: dict, events: list[dict], *, now: dt.datetime) -> str:
    """Fail loudly when a near-deadline advisory packet lags the next gameweek."""
    packet = rec.get("packet_status")
    if packet in ("safe_hold", "needs_refresh"):
        return "hold (currency check not applicable)"
    target = next_deadline(events, now=now)
    if target is None:
        return "no upcoming deadline in catalogue; skipped"
    gameweek, deadline = target
    hours_left = (deadline - now).total_seconds() / 3600
    rec_gw = rec.get("gameweek")
    if 0 <= hours_left <= PLAN_CURRENCY_WINDOW_H and rec_gw != gameweek:
        raise RuntimeError(
            f"Recommendation is GW{rec_gw} with {hours_left:.1f}h to the GW{gameweek} deadline"
        )
    return f"GW{rec_gw} vs next GW{gameweek} ({hours_left:.1f}h out)"


def main(*, lightweight: bool = False) -> int:
    now = dt.datetime.now(dt.timezone.utc)
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
    primary_rec: dict | None = None
    for league_id in (58005, 131997):
        for route in ("recommendations", "decision"):
            status, body, _ = fetch(f"{API}/v1/{route}/current?league_id={league_id}", 250_000)
            rec = json.loads(body)
            assert status == 200, rec
            validate_recommendation(rec)
            if league_id == 58005 and route == "recommendations":
                primary_rec = rec
            fresh = rec.get("freshness") or {}
            print(f"{route} L{league_id}: status={fresh.get('status')} packet={rec.get('packet_status')} "
                  f"prov={fresh.get('rank_provenance')} age_h={fresh.get('data_age_hours')}")

    status, body, headers = fetch(f"{API}/v1/leagues/58005/summary?page=1&page_size=50", 250_000)
    summary = json.loads(body)
    assert status == 200 and len(summary["managers"]) <= 50
    assert all("squad" not in manager for manager in summary["managers"])
    normalized_headers = {key.casefold(): value for key, value in headers.items()}
    assert normalized_headers.get("server-timing"), headers
    _, catalog_body, _ = fetch(f"{API}/v1/catalog/compact", 350_000)
    if primary_rec is not None:
        events = (json.loads(catalog_body).get("events") or []) if catalog_body else []
        print(f"plan currency L58005: {check_plan_currency(primary_rec, events, now=now)}")
    if lightweight:
        # Login HTML proves Netlify is serving the application without causing
        # an SSR fetch of multi-megabyte league data.
        fetch(f"{SITE}/sign-in", 250_000)
    else:
        fetch(f"{SITE}/league", 1_500_000)
        fetch(f"{SITE}/compare", 1_500_000)
        fetch(f"{SITE}/journal", 1_500_000)
    print("Production readiness, contracts and payload budgets passed")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lightweight", action="store_true", help="Skip data-heavy SSR page checks")
    raise SystemExit(main(lightweight=parser.parse_args().lightweight))
