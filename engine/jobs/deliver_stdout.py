#!/usr/bin/env python3
"""FPL Autopilot - deliver non-empty stdout to the configured Telegram chat.

Replicates Hermes no_agent cron semantics on a plain server:
  - empty stdin  -> exit 0, send NOTHING (silent)
  - non-empty stdin -> send the text as one Telegram message, exit 0

Usage (systemd ExecStart or shell):
  /opt/fpl-autopilot/.venv/bin/python jobs/job.py | \\
      /opt/fpl-autopilot/.venv/bin/python jobs/deliver_stdout.py --tag "job name"

Reads TELEGRAM_BOT_TOKEN from config/credentials.env and the target chat from
config/settings.json (telegram.chat_id, fallback: owner user id). Messages
longer than Telegram's 4096-char limit are split.
"""
import argparse
import json
import os
import sys

import project_paths

BASE = str(project_paths.resolve_project_root(__file__))


def load_creds():
    creds = {}
    with open(os.path.join(BASE, "config", "credentials.env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds


def load_settings():
    with open(os.path.join(BASE, "config", "settings.json"), encoding="utf-8") as f:
        return json.load(f)


def send(token, chat_id, text):
    import urllib.parse
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    ).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="FPL job")
    args = ap.parse_args()

    raw = sys.stdin.read()
    text = raw.strip()
    if not text:
        return 0  # silent - nothing changed

    creds = load_creds()
    token = creds.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("deliver_stdout: TELEGRAM_BOT_TOKEN missing", file=sys.stderr)
        return 1

    settings = load_settings()
    chat_id = (settings.get("telegram") or {}).get("chat_id")
    if not chat_id:
        owner = (settings.get("telegram") or {}).get("allowed_user_ids") or []
        chat_id = owner[0] if owner else None
    if not chat_id:
        print("deliver_stdout: no chat_id in settings", file=sys.stderr)
        return 1

    header = f"[{args.tag}]"
    body = f"{header}\n\n{text}"

    # Telegram hard cap: 4096 chars per message - split on paragraph boundaries.
    chunks, cur = [], ""
    for line in body.splitlines():
        if len(cur) + len(line) + 1 > 3900 and cur:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
        if len(cur) >= 3900:
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)

    for c in chunks:
        send(token, chat_id, c)
    return 0


if __name__ == "__main__":
    sys.exit(main())