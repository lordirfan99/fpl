"""
FPL Autopilot - auth canary (bi-weekly, silent when healthy).

PURPOSE: catch silent breakage in the Camoufox/PingFederate auth flow BEFORE
a deadline matters. The keepalive refreshes every 2h but only exercises the
*refresh* path; the browser-login path (Camoufox + Cloudflare + PingFederate
form-forcing) is reverse-engineered and can break upstream without warning.
This canary forces a full re-login and verifies the result on a slow cadence
so any upstream change is discovered with weeks of runway, not on deadline day.

Flow:
  1. Run browser_login.py --login (full Camoufox OIDC flow, ~30s).
  2. Verify the resulting session hits /api/me/ (player.entry present).
  3. Silent (exit 0, no stdout) when healthy. Print + exit 1 when broken.

Run: .venv/Scripts/python.exe jobs/auth_canary.py
Cron: fpl-auth-canary, every 2 weeks, no_agent, deliver=origin.
"""
import os
import sys
import subprocess

from project_paths import resolve_project_root, venv_python

BASE = str(resolve_project_root(__file__))
sys.path.insert(0, os.path.join(BASE, "execution"))

from fpl_client import FPLClient  # noqa: E402


def verify():
    """True if the current session can hit an authed endpoint."""
    try:
        me = FPLClient().get_json("me/")
        return (me.get("player") or {}).get("entry") is not None
    except Exception:
        return False


def main():
    # Force a full browser re-login (this is the fragile upstream path we're canarying)
    py = venv_python(BASE)
    r = subprocess.run(
        [py, os.path.join(BASE, "execution", "browser_login.py"), "--login"],
        capture_output=True, text=True, timeout=300, cwd=BASE,
    )
    if r.returncode != 0:
        print(f"AUTH CANARY FAIL: browser_login rc={r.returncode} tail={r.stdout[-300:]}")
        return 1
    if verify():
        # Healthy - silent (cron delivers nothing)
        return 0
    print("AUTH CANARY FAIL: re-login succeeded but /api/me/ verification failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
