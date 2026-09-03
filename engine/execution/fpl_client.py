"""
FPL authenticated client - auth status: OIDC browser session required.

AUTH FINDINGS (verified 04 Aug 2026):
  - Legacy login (users.premierleague.com/accounts/login/) is DEAD - DNS does not resolve.
  - The site now uses OIDC via https://account.premierleague.com/as (PingFederate).
  - password grant: DISABLED platform-wide (unsupported_grant_type).
  - device_code grant: server supports it but web client bfcbaf69-... is NOT registered
    for it (unauthorized_client).
  - authorization_code + PKCE works, but completing it requires a REAL browser
    session through Cloudflare (login UI is bot-protected).
  - Solution in progress: Camoufox/Playwright browser login -> harvest session
    cookies + bearer token -> cache in config/fpl_session.json -> reuse.

Read-only public API (no auth): bootstrap-static, fixtures, element-summary,
entry/{id}/history, entry/{id}/event/{gw}/picks - fully usable now.
"""
import os
import json
import base64
import hashlib
import secrets
import sys

import requests

BASE = "https://fantasy.premierleague.com/api/"
OIDC_BASE = "https://account.premierleague.com/as"
CLIENT_ID = "bfcbaf69-aade-4c1b-8f00-c1cb8a193030"
REDIRECT_URI = "https://fantasy.premierleague.com/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "Chrome/126.0 Safari/537.36")
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
SESSION_FILE = os.path.join(_CONFIG_DIR, "fpl_session.json")


def load_credentials():
    creds = {}
    with open(os.path.join(_CONFIG_DIR, "credentials.env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds


def oidc_discovery():
    r = requests.get(f"{OIDC_BASE}/.well-known/openid-configuration", timeout=30,
                     headers={"User-Agent": UA})
    r.raise_for_status()
    return r.json()


def build_authorize_url():
    """Build /as/authorize URL with PKCE for the browser-session flow.
    Returns (url, verifier, state)."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    conf = oidc_discovery()
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    import urllib.parse
    return conf["authorization_endpoint"] + "?" + urllib.parse.urlencode(params), verifier, state


def save_session(payload):
    with open(SESSION_FILE, "w") as f:
        json.dump(payload, f, indent=1)


def refresh_access_token(refresh_token=None):
    """Exchange the refresh token for a fresh access token. Returns token dict."""
    import requests as _rq
    session = load_session() or {}
    rt = refresh_token or session.get("refresh_token")
    if not rt:
        return None
    conf = oidc_discovery()
    r = _rq.post(conf["token_endpoint"], data={
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": CLIENT_ID,
    }, headers={"User-Agent": UA}, timeout=30)
    if r.status_code != 200:
        return None
    tok = r.json()
    session.update({k: tok.get(k) for k in
                    ["access_token", "refresh_token", "expires_in", "id_token"] if tok.get(k)})
    save_session(session)
    return tok


def load_session():
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE) as f:
            return json.load(f)
    return None


class FPLClient:
    """Session holder - works with either cookies or bearer token from session file."""

    def __init__(self, session_data=None):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        # ``{}`` explicitly means anonymous/public-only. Do not replace it
        # with a possibly stale cached auth session.
        self.session_data = (load_session() or {}) if session_data is None else session_data
        if self.session_data.get("cookies"):
            self.s.cookies.update(self.session_data["cookies"])
        if self.session_data.get("access_token"):
            self.s.headers["Authorization"] = f"Bearer {self.session_data['access_token']}"

    @property
    def authenticated(self):
        return bool(self.session_data.get("cookies")) or bool(self.session_data.get("access_token"))

    def reload(self):
        """Re-read the session file and refresh BOTH bearer credentials and cookies.

        Call after refresh_access_token() or a browser re-login: the in-memory
        session may hold cookies from an older login, and the write API requires
        session cookies (Bearer alone returns 403 Authentication credentials
        were not provided).
        """
        fresh = load_session() or {}
        self.session_data = fresh
        self.s.cookies.clear()
        if fresh.get("cookies"):
            self.s.cookies.update(fresh["cookies"])
        if fresh.get("access_token"):
            self.s.headers["Authorization"] = f"Bearer {fresh['access_token']}"
        else:
            self.s.headers.pop("Authorization", None)
        return self

    def get_json(self, path, timeout=30):
        r = self.s.get(BASE + path, timeout=timeout)
        if r.status_code == 401:
            tok = refresh_access_token()
            self.reload()  # refresh cookies too, not just the bearer header
            r = self.s.get(BASE + path, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def me(self):
        return self.get_json("me/")

    def my_team(self, team_id):
        return self.get_json(f"my-team/{team_id}/")

    def entry_history(self, team_id):
        return self.get_json(f"entry/{team_id}/history/")

    def entry_picks(self, team_id, gw):
        return self.get_json(f"entry/{team_id}/event/{gw}/picks/")

    def entry_transfers(self, team_id):
        """Public completed-deadline transfer history for an entry."""
        return self.get_json(f"entry/{team_id}/transfers/")

    def set_lineup(self, team_id, picks, chip=None, timeout=60):
        """POST a starting XI + bench + captain/vice. No transfers, reversible.

        ``picks`` is the /api/my-team payload: 15 rows with element, position
        (1-11 start, 12-15 bench), multiplier (2 captain / 1 start / 0 bench),
        is_captain, is_vice_captain. Refreshes auth first - the write API needs
        session cookies, not just a bearer token. Returns the requests.Response.
        """
        tok = refresh_access_token()
        self.reload()
        if tok and tok.get("access_token"):
            self.s.headers["Authorization"] = f"Bearer {tok['access_token']}"
        return self.s.post(
            f"{BASE}my-team/{team_id}/",
            json={"picks": picks, "chip": chip},
            headers={"Content-Type": "application/json", "User-Agent": UA},
            timeout=timeout,
        )


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--status"
    if mode == "--status":
        client = FPLClient()
        print("session file:", "EXISTS" if os.path.exists(SESSION_FILE) else "none")
        print("authenticated:", client.authenticated)
        conf = oidc_discovery()
        print("OIDC issuer:", conf.get("issuer"))
        print("auth endpoint:", conf.get("authorization_endpoint"))
        print("grants:", conf.get("grant_types_supported"))
        print("\nPublic API check (no auth):")
        me = client.get_json("me/")
        print("  /api/me/ ->", json.dumps(me)[:120])
    elif mode == "--authurl":
        url, verifier, state = build_authorize_url()
        print("AUTHORIZE URL (open in browser):")
        print(url[:500])
        print("\nKEEP THIS VERIFIER SAFE (needed to exchange code):")
        print(verifier[:40] + "...")
        print("verifier full length:", len(verifier))
        print("state:", state)


if __name__ == "__main__":
    main()
