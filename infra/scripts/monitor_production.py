"""Bounded synthetic production monitor with payload budgets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = os.getenv("FPL_API_BASE_URL", "https://sportmania.duckdns.org/fpl-scout-api").rstrip("/")
SITE = os.getenv("FPL_SITE_URL", "https://fpl-scout-intelligence.netlify.app").rstrip("/")
PUBLIC_DATA = os.getenv("FPL_PUBLIC_DATA_URL", "https://lordirfan99.github.io/fpl").rstrip("/")
RETRYABLE_HTTP_STATUS = {502, 503, 504}


def fetch(url: str, limit: int) -> tuple[int, bytes, dict[str, str]]:
    request = Request(url, headers={"User-Agent": "FPLScoutMonitor/1.0"})
    # Wait past the API's 30-second failed snapshot cache before the final try.
    retry_delays = iter((2, 32))
    while True:
        try:
            with urlopen(request, timeout=60) as response:
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise RuntimeError(f"payload budget exceeded: {url} > {limit} bytes")
                return response.status, body, dict(response.headers)
        except (URLError, TimeoutError) as error:
            context = f"GET {url}"
            if isinstance(error, HTTPError):
                request_id = error.headers.get("X-Request-ID") if error.headers is not None else None
                error.close()
                if request_id:
                    context += f" (request ID {request_id})"
                transient = error.code in RETRYABLE_HTTP_STATUS
            else:
                transient = isinstance(error, TimeoutError) or isinstance(error.reason, TimeoutError)
            delay = next(retry_delays, None)
            if not transient or delay is None:
                error.add_note(context)
                raise
            print(f"{context}: {error}. Retrying in {delay}s.", flush=True)
        sleep(delay)



def check_page(path: str, expected_text: str, limit: int = 750_000, *, home_redirect: bool = False) -> None:
    # A 200 status alone is not a working App Router page; error boundaries
    # and redirect shells can also be HTTP 200. Check useful SSR content.
    status, body, _ = fetch(f"{SITE}{path}", limit)
    html = body.decode("utf-8", errors="replace")
    if home_redirect and status == 200 and expected_text not in html:
        # Next.js can stream a redirect as HTTP 200 plus a refresh meta tag.
        # Accept only the intended homepage destination, then verify its content.
        class RedirectParser(HTMLParser):
            destination_ok = False

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == "meta" and (values.get("http-equiv") or "").lower() == "refresh":
                    content = values.get("content") or ""
                    self.destination_ok |= bool(re.fullmatch(r"\s*\d+\s*;\s*url=/this-week\s*", content))

        redirect = RedirectParser()
        redirect.feed(html)
        if redirect.destination_ok:
            check_page("/this-week", expected_text, limit)
            return
    if (status != 200 or expected_text not in html or
            any(error in html for error in ("We could not load this view.", "This view could not load", "Application error", "Recorded evidence is unavailable"))):
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

    # Public data was migrated from the VM. Probing legacy /summary and
    # /catalog routes would give false failures and miss a broken new homepage.
    status, body, _ = fetch(f"{PUBLIC_DATA}/manifest.json", 125_000)
    manifest = json.loads(body)
    release = manifest.get("release")
    files = manifest.get("files") or {}
    if (status != 200 or manifest.get("schema_version") != 1
            or manifest.get("writes_enabled") is not False
            or not isinstance(release, str) or not re.fullmatch(r"[a-f0-9]{64}", release)):
        raise RuntimeError("Public Pages manifest is unavailable or unverified")
    required = ("catalog.json", "fixtures.json", "leagues/58005/live.json",
                "leagues/131997/live.json", "journal/2026-27/index.json")
    if not all(isinstance(files.get(name), str) and re.fullmatch(r"[a-f0-9]{64}", files[name])
               for name in required):
        raise RuntimeError("Required public data artifacts are missing")
    captured_at = manifest.get("generated_at")
    if not isinstance(captured_at, str):
        raise RuntimeError("Public data capture time missing")
    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    seconds_old = (datetime.now(timezone.utc) - captured).total_seconds()
    if not -300 <= seconds_old <= 12 * 3600:
        raise RuntimeError(f"Public Pages data stale or future capture: age={seconds_old / 3600:.1f}h")
    status, catalog, _ = fetch(f"{PUBLIC_DATA}/releases/{release}/catalog.json", 500_000)
    if status != 200 or hashlib.sha256(catalog).hexdigest() != files["catalog.json"]:
        raise RuntimeError("Public player catalog artifact integrity failed")

    # Independently verify both league artifacts: HTTP 200 error states
    # are not acceptable as evidence of a healthy data release.
    for league_id in (58005, 131997):
        filename = f"leagues/{league_id}/live.json"
        league_status, league_body, _ = fetch(
            f"{PUBLIC_DATA}/releases/{release}/{filename}", 20_000_000)
        if league_status != 200 or hashlib.sha256(league_body).hexdigest() != files[filename]:
            raise RuntimeError(f"League {league_id} integrity failed")

    # V1 is the default homepage; explicit V2 research routes remain available.
    check_page("/", "This gameweek", 1_500_000, home_redirect=True)
    check_page("/v2/league?league=131997", "Complete standings", 1_500_000)
    check_page("/v2/elite?league=131997", "The top five percent", 1_500_000)
    if lightweight:
        check_page("/sign-in", "Your private decision room")
        check_page("/this-week", "This gameweek")
        check_page("/settings", "Your dashboard preferences")
    else:
        check_page("/v2/team", "My FPL team", 1_500_000)
        check_page("/v2/players", "Official recorded player pool", 1_500_000)
        check_page("/v2/compare?league=131997", "Compare recorded managers", 1_500_000)
        check_page("/v2/planner", "Five gameweeks ahead", 1_500_000)
        check_page("/v2/journal", "Season decision journal", 1_500_000)
        check_page("/settings", "Your dashboard preferences")
    print("Production readiness, contracts and payload budgets passed")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lightweight", action="store_true", help="Skip data-heavy SSR page checks")
    raise SystemExit(main(lightweight=parser.parse_args().lightweight))
