#!/usr/bin/env python3
"""
FPL Autopilot - auto-runner (no_agent cron script, silent by default).

Runs every 2h. Two triggers/actions:
  1. PRE-DEADLINE: refresh one run-bound V4.1 snapshot and build the approval plan.
  2. POST-GW: if a gameweek finished since the last review, run review.

Silent when no trigger fires. State kept in data/processed/auto_state.json.
"""
import json
import os
import subprocess
import datetime
import urllib.request
import sys
import uuid

import telegram_notify
from project_paths import resolve_project_root, venv_python

BASE = str(resolve_project_root(__file__))
sys.path.insert(0, os.path.join(BASE, "model"))
from league_alerts import alert_signature, meaningful_league_alerts

VENV_PY = venv_python(BASE)
STATE_FILE = os.path.join(BASE, "data", "processed", "auto_state.json")
LEAGUE_STATE_FILE = os.path.join(BASE, "data", "processed", "league_intelligence", "latest.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def load_creds():
    creds = {}
    with open(os.path.join(BASE, "config", "credentials.env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds


def send_telegram(text, chat_id, token):
    return telegram_notify.send_message(token, chat_id, text, log_prefix="[auto] ")


def run(script, args=None, env=None):
    cmd = [VENV_PY, os.path.join(BASE, "jobs", script)] + (args or [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
        return r.returncode, (r.stdout or "")[-4000:], (r.stderr or "")[-1000:]
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
        return 124, stdout[-4000:], (stderr + f"\n{script} timed out after 600 seconds")[-1000:]


def latest_reviewable_event(bootstrap):
    """Only calibrate against official results after FPL has data-checked them."""
    rows = [event for event in bootstrap.get("events", [])
            if event.get("finished") and event.get("data_checked")]
    return max((int(event["id"]) for event in rows), default=0)


def should_generate_plan(state, gw, hours_to_deadline, _unused=None):
    del state, gw
    return hours_to_deadline < 26


def main():
    settings = None
    try:
        with open(os.path.join(BASE, "config", "settings.json")) as f:
            settings = json.load(f)
    except Exception:
        pass

    now = datetime.datetime.now(datetime.timezone.utc)
    run_id = "v41-" + now.strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    run_env = os.environ.copy()
    run_env["FPL_RUN_ID"] = run_id
    refresh_failures = []
    state = load_state()
    reported = []

    try:
        bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    except Exception as e:
        print(f"[auto] bootstrap fetch failed: {repr(e)[:120]}")
        return

    # Refresh public, read-only league intelligence before any plan is built.
    # Failure never blocks the core xPts/approval pipeline; plan generation
    # then falls back to Neutral mode with no opponent-driven adjustment.
    if (settings or {}).get("league_intelligence", {}).get("enabled", False):
        previous_league = load_json(LEAGUE_STATE_FILE)
        lirc, liout, lierr = run("league_intelligence.py", ["--notifications-disabled"], env=run_env)
        if lirc == 0:
            state["league_intelligence_refreshed_at"] = now.isoformat()
            current_league = load_json(LEAGUE_STATE_FILE)
            alerts = meaningful_league_alerts(previous_league, current_league)
            signature = alert_signature(alerts) if alerts else None
            if alerts and signature != state.get("league_alert_signature"):
                try:
                    chat = (settings or {}).get("telegram", {}).get("chat_id")
                    token = load_creds().get("TELEGRAM_BOT_TOKEN")
                    if chat and token and send_telegram("🏆 LEAGUE WAR ROOM\n" + "\n".join(alerts), chat, token):
                        state["league_alert_signature"] = signature
                        state["league_alerted_at"] = now.isoformat()
                        reported.append("league alert")
                except Exception as exc:
                    # Intelligence notifications are advisory: missing/broken
                    # Telegram credentials must never block plan generation.
                    print(f"[auto] league alert delivery failed: {repr(exc)[:120]}")
            # Finalized dashboard snapshots are written directly to GCS by the
            # Cloud Run finalizer.  Do not call the retired VM -> API publisher
            # here: the API is read-only and has no snapshot-ingest endpoint.
            # League intelligence above remains local input to the plan.
        else:
            refresh_failures.append("league intelligence")
            print(f"[auto] league intelligence failed rc={lirc}: {(lierr or liout)[-500:]}")

    # --- trigger 1: synchronized canonical V4.1 decision ---
    next_gw = None
    for ev in bootstrap["events"]:
        dl = datetime.datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
        if not ev["finished"] and dl > now:
            next_gw = (ev, dl)
            break
    if next_gw:
        ev, dl = next_gw
        hrs = (dl - now).total_seconds() / 3600
        if should_generate_plan(state, ev["id"], hrs):
            print(f"[auto] GW{ev['id']} deadline in {hrs:.1f}h - running canonical V4.1 pipeline")
            run_env["FPL_REFRESH_FAILURES"] = ",".join(refresh_failures)
            if (settings or {}).get("v42_candidate", {}).get("shadow_enabled", True):
                src, sout, serr = run("pre_deadline_shadow_v42.py", env=run_env)
                if src == 0:
                    state["v42_shadow_gw"] = ev["id"]
                    state["v42_shadow_generated_at"] = now.isoformat()
                    reported.append("V4.2 shadow")
                else:
                    # A candidate failure can never block the live champion.
                    print(f"[auto] V4.2 shadow failed rc={src}: {(serr or sout)[-500:]}")
            rc, out, err = run("pre_deadline_run.py", env=run_env)
            if rc == 0:
                state["plan_gw"] = ev["id"]
                state["plan_generated_at"] = now.isoformat()
                state["plan_run_id"] = run_id
                reported.append("pipeline")
            else:
                print(f"[auto] pipeline failed rc={rc}: {err[-500:]}")

    # --- trigger 2: post-GW review ---
    last_finished = latest_reviewable_event(bootstrap)
    if last_finished:
        if state.get("last_reviewed_gw", 0) < last_finished:
            print(f"[auto] GW{last_finished} finished - running review")
            rc, out, err = run("post_gw_review.py", [str(last_finished)])
            if rc == 0:
                state["last_reviewed_gw"] = last_finished
                reported.append(f"review GW{last_finished}")
                erc, eout, eerr = run("evaluate_v42_candidate.py")
                if erc == 0:
                    reported.append("V4.2 evaluation")
                else:
                    print(f"[auto] V4.2 evaluation failed rc={erc}: {(eerr or eout)[-500:]}")
                if settings:
                    chat = settings.get("telegram", {}).get("chat_id")
                    token = load_creds().get("TELEGRAM_BOT_TOKEN")
                    if chat and token:
                        send_telegram("📋 POST-GW REVIEW\n" + out, chat, token)
            else:
                print(f"[auto] review failed rc={rc}: {err[-500:]}")

    save_state(state)
    if reported:
        print(f"[auto] done: {', '.join(reported)}")


def _run_once_with_lock():
    """Serialize overlapping auto-runner invocations.

    The timer fires every 2h; `run()` allows a 600s child. A slow run must not
    have the next invocation race it on data/processed/*.json. A second run
    that can't get the lock exits quietly (0) — the timer will try again.
    """
    lock_path = os.path.join(BASE, "data", "processed", ".fpl_auto.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    handle = open(lock_path, "w")
    try:
        import fcntl
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("[auto] another auto-runner holds the lock — skipping this tick")
            return
    except ImportError:
        pass  # non-POSIX (local dev on Windows): best-effort, no lock
    try:
        main()
    finally:
        handle.close()


if __name__ == "__main__":
    _run_once_with_lock()
