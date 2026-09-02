# Telegram Mini App v1

The Mini App is a mobile-first dashboard for the existing FPL Autopilot engine. It does **not** move FPL credentials into the browser. The browser receives only display data and sends authenticated actions back to the Python backend.

## What v1 includes

- Formation/pitch view for the live XI.
- Captain/vice, xPts, squad value, bank and deadline summary.
- Player detail view with club, opponent, price, availability and xPts.
- Pending-plan view.
- Approval/rejection endpoints wired to the existing hardened bot execution flow.
- Telegram WebApp `initData` HMAC validation on every API request.
- Extra user allow-list for write actions. If the allow-list is empty, Approve/Reject fail closed.

## 1. Install dependencies

```bat
cd C:\Users\irfan\fpl-autopilot
.venv\Scripts\pip.exe install -r requirements.txt
```

## 2. Start locally

```bat
jobs\start_miniapp.cmd
```

Health check locally:

```text
http://127.0.0.1:8787/health
```

The dashboard intentionally refuses API access when opened directly in a normal browser because there is no signed Telegram `initData`.

## 3. Put it behind public HTTPS

Telegram Mini Apps require a public HTTPS URL. Keep Uvicorn bound to `127.0.0.1:8787` and expose it through a TLS reverse proxy/tunnel. A named Cloudflare Tunnel with a stable hostname is a good fit for the current always-on Windows host.

Example target:

```text
https://fpl.example.com  ->  http://127.0.0.1:8787
```

Do **not** expose FPL credentials, `credentials.env`, `fpl_session.json`, or the raw data directory through the web server.

## 4. Register the Mini App with BotFather

Because the bot is used from a Telegram **group**, do not use an inline `web_app` keyboard button directly; Telegram restricts that button type to private bot chats. Instead register a named Mini App for `@Fplnaf_bot` in BotFather and point it to the public HTTPS URL.

BotFather will give the app a Telegram deep link similar to:

```text
https://t.me/Fplnaf_bot/dashboard
```

That deep link can safely be posted in the group and still launches the Telegram Mini App environment with signed `initData`.

## 5. Configure URLs

Edit `config/settings.json`:

```json
"miniapp": {
  "public_url": "https://fpl.example.com",
  "telegram_app_url": "https://t.me/Fplnaf_bot/dashboard",
  "allowed_user_ids": [],
  "port": 8787
}
```

Post the group launcher:

```bat
.venv\Scripts\python.exe jobs\post_miniapp_button.py
```

## 6. Open once and authorize write access

Open the Mini App from Telegram. The Status tab identifies the authenticated Telegram account. Until `allowed_user_ids` is configured, the dashboard remains usable but write actions stay locked.

Add only the Telegram user IDs allowed to execute FPL changes:

```json
"allowed_user_ids": [123456789]
```

Restart Uvicorn after editing settings.

The existing Telegram text UI remains available as the fallback/control channel.

## Security model

1. The FPL session and Telegram bot token remain server-side.
2. Every API request must contain Telegram-signed `initData` no older than 10 minutes.
3. Read access requires a valid Telegram launch session.
4. Approve/Reject additionally require an explicit `allowed_user_ids` match.
5. Approval delegates to the existing `approve_plan()` implementation, so stale-plan, injury, price-change, deadline, approval-lock and final-state verification protections are preserved.
6. The backend never accepts arbitrary transfer payloads from the browser in v1. The Mini App can approve/reject only the server-generated pending plan.

## Test

```bat
.venv\Scripts\python.exe -m unittest tests.test_miniapp -v
```

Then run the existing full suite:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
