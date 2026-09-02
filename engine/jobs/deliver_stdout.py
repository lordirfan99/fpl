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
import telegram_notify

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
    settings = load_settings()
    chat_id = (settings.get("telegram") or {}).get("chat_id")
    if not chat_id:
        owner = (settings.get("telegram") or {}).get("allowed_user_ids") or []
        chat_id = owner[0] if owner else None

    body = f"[{args.tag}]\n\n{text}"
    # A delivery failure is logged inside send_long_message and never raises:
    # the upstream job already did its work; a missing card must not fail the
    # systemd unit or the whole `job | deliver_stdout` pipeline.
    telegram_notify.send_long_message(
        token, chat_id, body, log_prefix=f"[{args.tag}] "
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())