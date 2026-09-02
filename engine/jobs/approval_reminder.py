#!/usr/bin/env python3
"""
FPL Autopilot - approval reminder ladder (no_agent cron script, silent unless
a reminder actually fires).

WHAT IT DOES (the feature spec from the 7 Aug audit):
  - initial card: sent by pre_deadline_run.py itself when a plan is created.
  - unchanged reminder at T-6h:  the pending plan is still pending and the
    deadline is within 6 hours -> one reminder that the plan is unchanged.
  - urgent reminder at T-90m:    the pending plan is still pending and the
    deadline is within 90 minutes -> URGENT, approve/reject now.
  - no spam: each rung fires AT MOST ONCE per plan. A plan whose content
    changed (new signature) restarts the ladder. An approved/rejected/
    executed plan never gets a reminder.

approval_window_hours (settings.json) is ALSO enforced here as a LOUD
advisory: if the pending plan is older than the window, every reminder
carries a "plan too stale - run /simulate" warning (the hard gate itself is
in bot/telegram_bot.py::plan_staleness).

Run via cron every 30 min:
    .venv/Scripts/python.exe jobs/approval_reminder.py
Empty stdout = nothing happened (watchdog pattern).
"""
import json
import os
import sys
import datetime
import urllib.request
import urllib.parse

import telegram_notify
from project_paths import resolve_project_root

BASE = str(resolve_project_root(__file__))
PLAN_FILE = os.path.join(BASE, "data", "processed", "pending_plan.json")
STATE_FILE = os.path.join(BASE, "data", "processed", "reminder_state.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"

T6_HOURS = 6.0
T90_MINUTES = 90.0 / 60.0


def load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def save_json(path, obj):
    sys.path.insert(0, os.path.join(BASE, "execution"))
    from atomic_io import atomic_write_json
    atomic_write_json(path, obj)


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
    try:
        with open(os.path.join(BASE, "config", "settings.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def plan_signature(plan):
    """Content signature - ANY plan change restarts the reminder ladder."""
    return json.dumps({
        "transfers": [[t.get("element_out"), t.get("element_in")] for t in plan.get("transfers", [])],
        "starters": [p.get("id") for p in plan.get("target_starters", [])],
        "bench": [p.get("id") for p in plan.get("bench", [])],
        "captain": (plan.get("captain") or {}).get("id"),
        "vice": (plan.get("vice") or {}).get("id"),
        "gw": plan.get("gw"),
    }, sort_keys=True)


def send_telegram(text, chat_id, token):
    return telegram_notify.send_message(
        token, chat_id, text, parse_mode="HTML",
        reply_markup={"inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": "approve"},
            {"text": "❌ Reject", "callback_data": "reject"},
        ]]},
        log_prefix="[reminder] ",
    )


def hours_to_deadline(plan):
    """Hours until the plan's GW deadline (from plan deadline, else bootstrap)."""
    dl = plan.get("deadline")
    if not dl:
        try:
            req = urllib.request.Request(
                "https://fantasy.premierleague.com/api/bootstrap-static/",
                headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            for ev in data.get("events", []):
                if ev.get("id") == plan.get("gw") and ev.get("deadline_time"):
                    dl = ev["deadline_time"]
                    break
        except Exception:
            return None
    if not dl:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(dl).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 3600


def main():
    plan = load_json(PLAN_FILE)
    if not plan:
        return  # nothing pending -> stay silent (no spam)
    status = plan.get("status")
    if status not in (None, "pending"):
        # approved/rejected/executed/failed/verification_pending -> no spam
        return

    state = load_json(STATE_FILE, {})
    key = plan_signature(plan)
    if state.get("plan_key") != key:
        # new/changed plan -> restart the ladder (initial card already sent
        # by the pipeline; we only mark it so the later rungs can fire once)
        state = {"plan_key": key, "sent_initial": True,
                 "sent_t6": False, "sent_t90": False}

    hrs = hours_to_deadline(plan)
    if hrs is None:
        return  # cannot compute deadline -> stay silent

    # staleness advisory from approval_window_hours
    staleness = ""
    try:
        window_h = float(load_settings().get("approval_window_hours", 12))
    except Exception:
        window_h = 12.0
    gen = plan.get("generated_at")
    if gen:
        try:
            gen_dt = datetime.datetime.fromisoformat(str(gen).replace("Z", "+00:00"))
            age_h = (datetime.datetime.now(datetime.timezone.utc) - gen_dt).total_seconds() / 3600
            if age_h > window_h:
                staleness = (f"\n⚠️ Plan is {age_h:.1f}h old (approval window {window_h:g}h) — "
                             "too stale to trust. Run /simulate to regenerate BEFORE approving.")
        except ValueError:
            pass

    n_tr = len(plan.get("transfers", []))
    cap = (plan.get("captain") or {}).get("name", "?")
    head = f"🧠 <b>GW{plan.get('gw')} PLAN STILL PENDING</b> — {n_tr} transfer(s), captain {cap}"

    if hrs <= T90_MINUTES and not state.get("sent_t90"):
        mins = max(0, int(hrs * 60))
        text = (f"{head}\n\n🚨 <b>URGENT — deadline in ~{mins} min!</b>\n"
                "Approve now or the deadline passes with no changes.\n"
                f"Deadline: {plan.get('deadline')}{staleness}")
        send_telegram(text, load_settings().get("telegram", {}).get("chat_id"),
                      load_creds().get("TELEGRAM_BOT_TOKEN"))
        state["sent_t90"] = True
        # urgent supersedes the earlier rung - never also send T-6h after URGENT
        state["sent_t6"] = True
        save_json(STATE_FILE, state)
        print(f"[reminder] urgent T-{mins}m reminder sent for GW{plan.get('gw')}")
        return

    if hrs <= T6_HOURS and not state.get("sent_t6"):
        text = (f"{head}\n\n⏰ <b>Reminder — deadline in ~{hrs:.1f}h.</b>\n"
                "Plan unchanged since it was posted. Tap ✅ Approve or ❌ Reject.\n"
                f"Deadline: {plan.get('deadline')}{staleness}")
        send_telegram(text, load_settings().get("telegram", {}).get("chat_id"),
                      load_creds().get("TELEGRAM_BOT_TOKEN"))
        state["sent_t6"] = True
        save_json(STATE_FILE, state)
        print(f"[reminder] T-{hrs:.1f}h reminder sent for GW{plan.get('gw')}")
        return

    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
