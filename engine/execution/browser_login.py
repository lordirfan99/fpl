"""
Browser-session login via Camoufox - direct OIDC authorize flow with form forcing.

Why form forcing: PingFederate's login page regenerates the hidden `state`
(and form action) via page JS, so the completed authorize request may belong
to a DIFFERENT code_challenge than ours -> token exchange fails with
invalid_grant. Fix: right before submit, force the form action + hidden state
back to OUR authorize request, so the code we get matches OUR verifier.

The SPA-driven route (clicking Login on the FPL app) is broken in this env:
the /signon/ flow rejects with "Redirect URI mismatch".

Modes:
  --diagnose  : load authorize URL, dump login form structure (no creds used)
  --login     : full flow - force form, fill creds, submit, exchange, save session
Run: .venv/Scripts/python.exe execution/browser_login.py [--diagnose|--login]
"""
import asyncio
import json
import os
import re
import sys
import urllib.parse

import requests
from playwright.async_api import async_playwright
from camoufox.async_api import AsyncNewBrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fpl_client import build_authorize_url, oidc_discovery, CLIENT_ID, REDIRECT_URI, save_session

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


async def dump_page(page):
    return await page.evaluate("""() => ({
        url: location.href,
        title: document.title,
        body: (document.body ? document.body.innerText : '').slice(0, 500),
        inputs: [...document.querySelectorAll('input')].map(i => ({name: i.name, id: i.id, type: i.type})),
        formAction: (document.querySelector('form') ? document.querySelector('form').action : null),
        hiddenState: (document.querySelector('input[name="state"]') ? document.querySelector('input[name="state"]').value : null)
    })""")


async def run(mode):
    async with async_playwright() as p:
        browser = await AsyncNewBrowser(p, humanize=True, window=(1280, 720))
        page = await browser.new_page()

        url, verifier, state = build_authorize_url()
        print("authorize url:", url[:160])
        try:
            await page.goto(url, timeout=90000, wait_until="domcontentloaded")
        except Exception as e:
            print("goto warn:", repr(e)[:140])
        await page.wait_for_timeout(7000)
        info = await dump_page(page)
        print("\nLANDED:", info["url"][:170])
        print("INPUTS:", json.dumps(info["inputs"][:8]))
        print("formAction:", (info.get("formAction") or "")[:150])
        print("hiddenState:", (info.get("hiddenState") or "")[:60])

        if mode == "--diagnose":
            await browser.close()
            return

        # force the form to complete OUR authorize request
        forced = await page.evaluate("""(args) => {
            const f = document.querySelector('form');
            if (!f) return false;
            f.action = args.url;
            const st = f.querySelector('input[name="state"]');
            if (st) st.value = args.state;
            return true;
        }""", {"url": url, "state": state})
        print("form forced to our request:", forced)

        creds = {}
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "config", "credentials.env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    creds[k.strip()] = v.strip()

        for sel in ["#username", 'input[name="username"]', 'input[name="pf.username"]']:
            el = await page.query_selector(sel)
            if el:
                await el.fill(creds["FPL_LOGIN"])
                print("user filled via", sel)
                break
        for sel in ["#password", 'input[name="password"]', 'input[name="pf.pass"]']:
            el = await page.query_selector(sel)
            if el:
                await el.fill(creds["FPL_PASSWORD"])
                print("pass filled via", sel)
                break

        for sel in ['#onetrust-accept-btn-handler', '#onetrust-pc-btn-handler', '.onetrust-close-btn-handler']:
            el = await page.query_selector(sel)
            if el:
                try:
                    await el.click(timeout=3000)
                    print("consent dismissed via", sel)
                    break
                except Exception:
                    pass
        await page.wait_for_timeout(1200)

        submitted = await page.evaluate("""() => {
            const f = document.querySelector('form');
            if (!f) return false;
            const btn = f.querySelector('button[type="submit"], input[type="submit"]');
            if (btn) { btn.click(); return true; }
            return false;
        }""")
        print("form submitted:", submitted)
        if not submitted:
            await page.keyboard.press("Enter")

        # wait for redirect back to fantasy.premierleague.com (poll fast)
        landed = None
        for _ in range(40):
            await page.wait_for_timeout(1000)
            cur = page.url
            if cur.startswith("https://fantasy.premierleague.com"):
                landed = cur
                break
        if not landed:
            print("\nNO REDIRECT after 40s; url:", page.url[:170])
            info2 = await dump_page(page)
            print("BODY:", info2["body"][:600])
            await browser.close()
            return
        print("\nREDIRECTED:", landed[:220])

        m = re.search(r"[?&]code=([^&]+)", landed)
        if not m:
            print("NO CODE IN URL")
            await browser.close()
            return
        code = urllib.parse.unquote(m.group(1))
        print("CODE extracted, len", len(code))

        conf = oidc_discovery()
        r = requests.post(conf["token_endpoint"], data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code": code,
            "code_verifier": verifier,
        }, headers={"User-Agent": UA}, timeout=30)
        print("token exchange ->", r.status_code)
        if r.status_code == 200:
            tok = r.json()
            cookies = await page.context.cookies()
            save_session({
                "access_token": tok.get("access_token"),
                "refresh_token": tok.get("refresh_token"),
                "expires_in": tok.get("expires_in"),
                "cookies": {c["name"]: c["value"] for c in cookies},
            })
            print("SESSION SAVED | access:", bool(tok.get("access_token")),
                  "| refresh:", bool(tok.get("refresh_token")), "| expires_in:", tok.get("expires_in"))
            me = requests.get("https://fantasy.premierleague.com/api/me/",
                              headers={"Authorization": f"Bearer {tok['access_token']}", "User-Agent": UA},
                              timeout=30)
            print("api/me ->", me.status_code, "|", me.text[:300])
        else:
            print("exchange failed:", r.text[:300])
        await browser.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--login"
    asyncio.run(run(mode))
