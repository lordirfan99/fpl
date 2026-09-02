"""
FPL Autopilot - token keepalive (silent watchdog).

Keeps the OIDC session alive by refreshing the access token PROACTIVELY
every run (each refresh rotates to a NEW refresh token, so the refresh
token never reaches its server-side lifetime cap and never dies).

Flow:
  1. refresh_access_token() -> rotates pair
  2. Verify via /api/me/ (player.entry must be present) with RETRIES:
     the connection to the FPL API is intermittently reset by the ISP
     (TM Net 10054); a single verify() hits the blip and falsely reports
     failure even when the session is valid.
  3. If refresh fails with invalid_grant (refresh token dead server-side)
     -> auto re-login via execution/browser_login.py --login (Camoufox, ~30s)
  4. If login also fails after retries -> print alert (cron delivers it;
     silent otherwise).

Run: .venv/Scripts/python.exe jobs/token_keepalive.py
Cron: fpl-token-keepalive, every 2h, no_agent, silent-when-healthy.
"""
import os
import sys
import time
import subprocess

from project_paths import resolve_project_root, venv_python

BASE = str(resolve_project_root(__file__))
sys.path.insert(0, os.path.join(BASE, "execution"))

from fpl_client import refresh_access_token, FPLClient


def verify(retries=4, delay=5):
    """Return True if the current session can hit an authed endpoint.

    Retries across transient ISP connection resets (10054) — the FPL API
    usually succeeds within 1-3 attempts. This is the ONLY reliable check;
    browser_login may save a valid session and still exit rc=1, so we never
    trust the exit code alone.
    """
    for attempt in range(1, retries + 1):
        try:
            client = FPLClient()
            me = client.get_json("me/")
            entry = (me.get("player") or {}).get("entry")
            if entry is not None:
                return True
        except Exception:
            pass
        if attempt < retries:
            time.sleep(delay)
    return False


def main():
    # Path 1: proactive refresh (rotates tokens)
    refreshed = False
    try:
        tok = refresh_access_token()
        refreshed = bool(tok and tok.get("access_token"))
    except Exception:
        refreshed = False
    if refreshed and verify():
        return 0  # Healthy - silent. (No output = cron sends nothing.)

    # Path 2: full browser re-login
    try:
        py = venv_python(BASE)
        r = subprocess.run(
            [py, os.path.join(BASE, "execution", "browser_login.py"), "--login"],
            capture_output=True, text=True, timeout=300,
            cwd=BASE,
        )
        # Do NOT trust exit code: the real check is verify() with retries.
        # On success stay COMPLETELY silent (no_agent cron delivers any
        # non-empty stdout verbatim — even "re-login OK" would spam).
        if verify():
            return 0
        print(f"[{time.strftime('%H:%M')}] FPL re-login FAILED rc={r.returncode} tail={r.stdout[-200:]}")
    except Exception as e:
        print(f"[{time.strftime('%H:%M')}] FPL re-login crashed: {repr(e)[:120]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
