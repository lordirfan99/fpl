"""Post a Telegram message containing the Mini App launcher button.

Usage:
    .venv/Scripts/python.exe jobs/post_miniapp_button.py

For group chats Telegram requires a Mini App deep link configured through
BotFather (for example https://t.me/Fplnaf_bot/dashboard), rather than an
InlineKeyboardButton web_app payload which is private-chat only.
"""
import json
import os
import urllib.request

from project_paths import resolve_project_root

BASE = str(resolve_project_root(__file__))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_creds():
    creds = {}
    with open(os.path.join(BASE, "config", "credentials.env"), encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds


def main():
    settings = load_json(os.path.join(BASE, "config", "settings.json"))
    miniapp = settings.get("miniapp") or {}
    app_url = str(miniapp.get("telegram_app_url", "")).strip()
    if not app_url.startswith("https://t.me/"):
        raise SystemExit(
            "miniapp.telegram_app_url must be the BotFather Mini App deep link "
            "(example: https://t.me/Fplnaf_bot/dashboard)"
        )

    token = load_creds().get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing")

    chat_id = settings["telegram"]["chat_id"]
    payload = {
        "chat_id": chat_id,
        "text": "📱 <b>FPL Autopilot Dashboard</b>\nOpen the full Mini App for squad, xPts, fixtures and pending plans.",
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [[{
                "text": "📱 Open FPL Dashboard",
                "url": app_url
            }]]
        }
    }

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise SystemExit(f"Telegram rejected launcher message: {result}")
    print(f"Mini App launcher posted. message_id={result['result']['message_id']}")


if __name__ == "__main__":
    main()
