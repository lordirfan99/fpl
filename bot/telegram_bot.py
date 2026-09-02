"""
FPL Autopilot - Telegram bot service (interactive control panel).

Commands:
  /start  - welcome + mode explanation
  /status - squad value, bank, free transfers, next deadline countdown
  /team   - starting XI + bench with this-GW xPts
  /simulate - run the pre-deadline pipeline now and show the plan
  /approve  - execute the pending plan (transfers + lineup + captain)
  /reject   - discard the pending plan, keep squad as-is
  /chip <name> - stage a chip (wildcard|freehit|benchboost|triplecaptain)
  /history - last 6 GWs: points, rank, rank change

Inline Approve/Reject buttons on the plan cards do the same as the commands.

Runs as a long-lived polling service:
  .venv/Scripts/python.exe bot/telegram_bot.py
"""
import asyncio
import html
import json
import os
import subprocess
import sys
import datetime
import hmac
import threading
import time
import uuid
import urllib.request

# Force UTF-8 stdout/stderr - Windows cp1252 cannot encode the 4-byte emoji
# used in bot messages and crashes the process at startup when launched via
# Task Scheduler / VBScript (clean env without PYTHONUTF8).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "optimizer"))
sys.path.insert(0, os.path.join(BASE, "jobs"))

from fpl_client import FPLClient
from xpts_model import inseason_xpts_from_bootstrap, preseason_xpts
from plan_validation import InvalidPlanError, validate_plan
from proposal_binding import (
    canonical_plan_hash, is_past_cutoff, short_id)
sys.path.insert(0, os.path.join(BASE, "bot"))
from templates import status_message, team_message, plan_card, history_message
from project_paths import venv_python

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
POS_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
PLAN_FILE = os.path.join(BASE, "data", "processed", "pending_plan.json")
LEAGUE_REGISTRY_FILE = os.path.join(BASE, "config", "league_registry.json")
LEAGUE_REQUEST_FILE = os.path.join(BASE, "data", "processed", "league_requests.json")
HEARTBEAT_FILE = os.path.join(BASE, "data", "processed", "bot_heartbeat.txt")
PYTHON = venv_python(BASE)
# Exclusive approval lock (QA hardening): serializes concurrent approve_plan()
# callbacks so two runs can't both read the plan as 'pending' before either
# writes 'executing'. data/processed/ is gitignored, so this never ships.
LOCK_FILE = os.path.join(BASE, "data", "processed", "approve.lock")
LOCK_STALE_SECONDS = 15 * 60
# FPL writes are deliberately opt-in at the service boundary.  A missing value
# is safe: it leaves Telegram fully usable for read-only planning, but prevents
# an accidental executor call after a new deployment.
EXECUTION_ENABLED_ENV = "FPL_TELEGRAM_EXECUTION_ENABLED"
DRY_RUN_ENV = "FPL_TELEGRAM_DRY_RUN"
# Keep/Exclude player preferences (config/player_prefs.json) - user pins
# players via bot buttons; the pipeline enforces them in the solver.
PREFS_FILE = os.path.join(BASE, "config", "player_prefs.json")
LEAGUE_STATE_FILE = os.path.join(
    BASE, "data", "processed", "league_intelligence", "latest.json"
)


def _read_league_registry():
    try:
        with open(LEAGUE_REGISTRY_FILE, encoding="utf-8") as source:
            payload = json.load(source)
        return payload if isinstance(payload, dict) else {"version": 1, "max_active": 10, "leagues": []}
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "max_active": 10, "leagues": []}


def _write_league_registry(payload):
    os.makedirs(os.path.dirname(LEAGUE_REGISTRY_FILE), exist_ok=True)
    tmp = LEAGUE_REGISTRY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as target:
        json.dump(payload, target, indent=2)
    os.replace(tmp, LEAGUE_REGISTRY_FILE)


def request_league(uid, league_id, friendly_name=None):
    if not authorized(uid):
        return "❌ Not authorized to request leagues."
    if league_id <= 0:
        return "❌ League ID must be a positive number."
    registry = _read_league_registry()
    leagues = registry.get("leagues") or []
    if any(int(row.get("league_id", -1)) == league_id for row in leagues):
        return f"ℹ️ League {league_id} is already in the tracking registry."
    active = sum(1 for row in leagues if row.get("status") == "active")
    if active >= int(registry.get("max_active") or 10):
        return f"❌ Maximum of {registry.get('max_active', 10)} active leagues reached."
    try:
        info = FPLClient().get_json(f"leagues-classic/{league_id}/standings/?page_standings=1")
        official = (info.get("league") or {}).get("name") or str(league_id)
        count = int((info.get("standings") or {}).get("results") and len(info["standings"]["results"]) or 0)
    except Exception as error:
        return f"❌ Could not validate league {league_id}: {repr(error)[:120]}"
    pending = {"league_id": league_id, "name": friendly_name or official, "official_name": official,
               "status": "pending", "tracking_mode": "full", "member_count_preview": count}
    requests = []
    try:
        with open(LEAGUE_REQUEST_FILE, encoding="utf-8") as source:
            requests = json.load(source)
        if not isinstance(requests, list): requests = []
    except (OSError, json.JSONDecodeError):
        pass
    requests = [row for row in requests if int(row.get("league_id", -1)) != league_id]
    requests.append(pending)
    os.makedirs(os.path.dirname(LEAGUE_REQUEST_FILE), exist_ok=True)
    with open(LEAGUE_REQUEST_FILE, "w", encoding="utf-8") as target:
        json.dump(requests, target, indent=2)
    return (f"📥 League request created: <b>{html.escape(pending['name'])}</b> (L{league_id})\n"
            "It is pending owner confirmation before tracking starts.")

# Kept outside main() so the callback contract can be unit-tested without
# creating a Telegram Application or requiring live credentials.
WAR_ROOM_SECTIONS = (
    ("🎯 Prize Race", "war_prize"),
    ("🕵️ Sharp Rivals", "war_rivals"),
    ("🔄 Transfer Radar", "war_transfers"),
    ("👑 Captain Battle", "war_captain"),
    ("💷 Market Watch", "war_market"),
    ("🔒 Deadline Registry", "war_registry"),
    ("⚔️ Attack Plan", "war_attack"),
    ("🔄 Refresh Data", "war_refresh"),
)
MARKET_SECTIONS = (
    ("🔻 Fall risks", "war_market_fall"),
    ("🔺 Rise watch", "war_market_rise"),
    ("🚑 Availability", "war_market_availability"),
)
_LEAGUE_REFRESH_LOCK = threading.Lock()


def load_player_prefs():
    """{keep: [ids], exclude: [ids]} from config/player_prefs.json (safe)."""
    if os.path.exists(PREFS_FILE):
        try:
            with open(PREFS_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return {
                "keep": [int(x) for x in d.get("keep", [])],
                "exclude": [int(x) for x in d.get("exclude", [])],
            }
        except Exception:
            pass
    return {"keep": [], "exclude": []}


def save_player_prefs(prefs):
    from atomic_io import atomic_write_json
    atomic_write_json(PREFS_FILE, prefs)


def model_version_line():
    """Show the only supported decision authority in Telegram."""
    pending = load_pending()
    if pending and pending.get("model_version") == "competitive-v4.0":
        return "competitive-v4.0 (canonical decision packet)"
    return "competitive-v4.0 (awaiting fresh decision packet)"


def load_settings():
    with open(os.path.join(BASE, "config", "settings.json")) as f:
        return json.load(f)


def load_creds():
    creds = {}
    with open(os.path.join(BASE, "config", "credentials.env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds


# ---------------------------------------------------------------------------
# Authorization (Sol audit P0-1): immutable Telegram USER-ID allowlist.
# Chat-level allowlisting is a routing control, NOT authorization. Every
# privileged boundary (approve/reject/chip/keep/exclude) checks the caller's
# immutable Telegram user id against settings.telegram.allowed_user_ids.
# Empty list = FAIL CLOSED (no privileged actions until the owner is added).
# ---------------------------------------------------------------------------
USER_ID_LOG = os.path.join(BASE, "data", "bot_user_ids.log")


def allowed_user_ids():
    try:
        return set(load_settings().get("telegram", {}).get("allowed_user_ids", []))
    except Exception:
        return set()


def record_user_id(uid):
    """Append a previously-unknown caller user id to the discovery log so the
    owner can be added to allowed_user_ids (bootstrap, fail-closed default)."""
    try:
        if uid is None:
            return
        known = set()
        if os.path.exists(USER_ID_LOG):
            with open(USER_ID_LOG, encoding="utf-8") as f:
                known = {int(x) for x in f.read().split() if x.strip().isdigit()}
        if int(uid) in known:
            return
        with open(USER_ID_LOG, "a", encoding="utf-8") as f:
            f.write(f"{int(uid)}\n")
    except Exception:
        pass


def authorized(uid):
    """True only when uid is an immutable allowed Telegram user id."""
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return False
    allow = allowed_user_ids()
    if uid in allow:
        return True
    record_user_id(uid)
    return False


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def load_pending():
    if os.path.exists(PLAN_FILE):
        try:
            with open(PLAN_FILE) as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_pending(plan):
    # P0.10 (7 Aug audit): unified atomic writer - write temp + os.replace
    # so a concurrent reader never sees half-written JSON. pre_deadline_run
    # and the bot now share execution/atomic_io.py.
    from atomic_io import atomic_write_json
    atomic_write_json(PLAN_FILE, plan)


def next_deadline_info():
    bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    now = datetime.datetime.now(datetime.timezone.utc)
    for ev in bootstrap["events"]:
        dl = datetime.datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
        if not ev["finished"] and dl > now:
            hrs = (dl - now).total_seconds() / 3600
            return ev["id"], ev["deadline_time"], hrs
    return None, None, None


def status_text():
    client = FPLClient()
    settings = load_settings()
    team_id = settings["team_id"]
    team = client.my_team(team_id)
    tr = team.get("transfers", {})
    gw, dl, hrs = next_deadline_info()
    # value lives under transfers (my-team API: no top-level "value" key).
    squad_value = tr.get("value") if tr.get("value") is not None else team.get("value", 0)
    # unlimited status = pre-season open transfers; limit is None then.
    if tr.get("status") == "unlimited":
        ft_display = "99 (unlimited)"
    else:
        limit = tr.get("limit")
        made = tr.get("made") or 0
        ft_display = f"{max(1, (limit or 1) - made)} (made {made})"
    lines = [
        ("Team value", f"£{squad_value / 10:.1f}m"),
        ("Bank", f"£{(tr.get('bank', 0) or 0) / 10:.1f}m"),
        ("Free transfers", ft_display),
        ("Players", f"{len(team.get('picks', []))}/15"),
        ("Model", model_version_line()),
    ]
    if gw:
        lines.append(("Next deadline", f"GW{gw} in {hrs:.1f}h ({dl})"))
    pending = load_pending()
    if pending and pending.get("status") == "pending":
        pending_note = f"GW{pending.get('gw')} — {len(pending.get('transfers', []))} transfers"
    else:
        pending_note = None
    return status_message(lines, pending_note)


def team_text():
    client = FPLClient()
    settings = load_settings()
    bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    fixtures = fetch("https://fantasy.premierleague.com/api/fixtures/")
    els = {e["id"]: e for e in bootstrap["elements"]}
    gw = next((ev["id"] for ev in bootstrap["events"] if not ev["finished"]), 1)
    gw_so_far = max(0, gw - 1)
    fdr = {}
    for f in fixtures:
        if f.get("event") == gw:
            fdr[(f["event"], f["team_h"])] = f["team_h_difficulty"]
            fdr[(f["event"], f["team_a"])] = f["team_a_difficulty"]
    team = client.my_team(settings["team_id"])
    rows = []
    for p in sorted(team.get("picks", []), key=lambda x: x["position"]):
        e = els[p["element"]]
        f = fdr.get((gw, e["team"]), 3)
        xp = preseason_xpts(e, f) if gw_so_far == 0 else inseason_xpts_from_bootstrap(e, f, gw_so_far)
        role = "C" if p.get("is_captain") else ("VC" if p.get("is_vice_captain") else ("SUB" if p.get("multiplier") == 0 else ""))
        rows.append((str(p["position"]), POS_MAP[e["element_type"]], e["web_name"], f"£{e['now_cost'] / 10:.1f}m", f"{xp:.1f}", role))
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    return team_message(gw, rows) + f"\n\n📐 <i>V4 projection snapshot • {stamp} UTC</i>"


def run_pipeline():
    """Refresh all read-only inputs, then build one canonical V4 plan.

    /simulate is an operator-requested full refresh: official bootstrap/fixture
    data, league intelligence, and finally the planner. Refresh failures are
    retained as a short diagnostic; they never cause a transfer write.

    The competitive context is read live from the shared read-only API by
    pre_deadline_run itself — there is no VM->API publish step any more.
    """
    refresh_failures = []
    run_id = "v41-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    run_env = os.environ.copy()
    run_env["FPL_RUN_ID"] = run_id
    refresh_steps = [
        ("official FPL data", [PYTHON, os.path.join(BASE, "jobs", "daily_pull.py")]),
        ("league intelligence", [PYTHON, os.path.join(BASE, "jobs", "league_intelligence.py"), "--notifications-disabled"]),
    ]
    for label, command in refresh_steps:
        try:
            refreshed = subprocess.run(command, capture_output=True, text=True, timeout=180, env=run_env)
            if refreshed.returncode != 0:
                refresh_failures.append(label)
        except subprocess.TimeoutExpired:
            refresh_failures.append(f"{label} timeout")
        except OSError:
            refresh_failures.append(label)
    try:
        run_env["FPL_REFRESH_FAILURES"] = ",".join(refresh_failures)
        r = subprocess.run([PYTHON, os.path.join(BASE, "jobs", "pre_deadline_run.py")],
                           capture_output=True, text=True, timeout=300, env=run_env)
    except subprocess.TimeoutExpired:
        return "❌ Pipeline timed out while building the V4 plan. No FPL write was attempted."
    except OSError as exc:
        return "❌ Pipeline could not start: " + html.escape(str(exc)[:180])
    out = (r.stdout or "")[-4000:]
    if r.returncode != 0:
        # NEVER send raw tracebacks with parse_mode=HTML: Telegram rejects
        # unknown tags like <module> and the error card silently never arrives,
        # leaving the user staring at "🔁 Running pipeline...". Escape first.
        err = (r.stderr or out)[-1500:]
        return "❌ Pipeline failed:\n" + html.escape(err)
    plan = load_pending()
    if plan:
        card = card_text(plan)
        if refresh_failures:
            card += "\n\n⚠️ Refresh incomplete: " + ", ".join(refresh_failures) + "."
        return card
    return "⚠️ Pipeline ran but no plan was generated.\n" + html.escape(out[-800:])


def card_text(plan):
    return plan_card(plan)


def plan_staleness(plan):
    """Return a reason string if the pending plan is stale, else "".

    FAILS CLOSED (QA hardening): any inability to fully re-validate against
    LIVE bootstrap -> a reason string, so a plan is never executed on
    incomplete evidence:
      1. plan has no gameweek, or the GW is missing from live bootstrap
      2. deadline already passed
      3. any transfer-in or player in the target squad is now injured
         (status 'i'), unavailable / left league (status 'u'), or doubtful
         (chance_of_playing_next_round < 50)
      4. any player id referenced by the plan is missing from live bootstrap
      5. any transfer-in price has risen above the plan's purchase_price
    """
    try:
        bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
        elements = bootstrap.get("elements") or []
    except Exception as e:
        return f"could not re-validate vs live data ({repr(e)[:80]})"
    if not elements:
        return "live bootstrap returned no elements - cannot re-validate plan (fails closed)"

    els = {e["id"]: e for e in elements}

    # 1. gameweek must exist in live bootstrap (deadline unknown = fail closed)
    gw = plan.get("gw")
    if not gw:
        return "plan has no gameweek - cannot validate (run /simulate to regenerate)"
    ev = next((x for x in (bootstrap.get("events") or []) if x.get("id") == gw), None)
    if ev is None:
        return f"GW{gw} not found in live bootstrap - cannot validate deadline (fails closed)"
    dl = datetime.datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
    if datetime.datetime.now(datetime.timezone.utc) > dl:
        return f"GW{gw} deadline already passed ({ev['deadline_time']})"

    # Account-state binding: a transfer or squad edit made outside Telegram
    # must invalidate this proposal immediately.  The old check only compared
    # player availability, allowing a pre-transfer card to survive after the
    # owner's free transfer had already been used.
    try:
        live_team = FPLClient().my_team(int(plan.get("team_id")))
        live_ids = {int(p.get("element")) for p in (live_team.get("picks") or []) if p.get("element") is not None}
        expected_ids = {int(x) for x in (plan.get("pre_transfer_squad_ids") or [])}
        if expected_ids and live_ids != expected_ids:
            return "live squad changed after simulation (transfer or edit detected)"
        live_transfers = live_team.get("transfers") or {}
        if live_transfers.get("status") != "unlimited" and plan.get("free_transfers_before") is not None:
            limit = int(live_transfers.get("limit") or 1)
            made = int(live_transfers.get("made") or 0)
            live_ft = max(0, limit - made)
            if live_ft != int(plan.get("free_transfers_before")):
                return (f"free transfers changed from {plan.get('free_transfers_before')} "
                        f"to {live_ft} since simulation")
    except Exception as error:
        return f"could not re-validate live squad/transfer state ({repr(error)[:80]})"

    # P0.2 (7 Aug audit): enforce approval_window_hours - a plan generated too
    # long ago is stale even when no player has changed. The setting used
    # to do nothing; now it is a hard freshness gate. Run /simulate to refresh.
    try:
        window_h = float(load_settings().get("approval_window_hours", 12))
    except Exception:
        window_h = 12.0
    gen = plan.get("generated_at")
    if not gen:
        return "plan has no generated_at timestamp - cannot validate age (fails closed)"
    try:
        gen_dt = datetime.datetime.fromisoformat(str(gen).replace("Z", "+00:00"))
        age_h = (datetime.datetime.now(datetime.timezone.utc) - gen_dt).total_seconds() / 3600
    except ValueError:
        return "plan has an unreadable generated_at - cannot validate age (fails closed)"
    if age_h > window_h:
        return (f"plan is {age_h:.1f}h old (approval window {window_h:g}h) - "
                "too stale to trust (run /simulate to regenerate)")

    # 2. injuries/doubtful on everyone the plan touches (ins + starters + bench)
    involved = []
    for t in plan.get("transfers", []):
        involved.append(t.get("element_in"))
    for p in plan.get("target_starters", []):
        involved.append(p.get("id"))
    for p in plan.get("bench", []):
        involved.append(p.get("id"))
    for eid in involved:
        e = els.get(eid)
        if not e:
            return (f"player id {eid} not found in live bootstrap - "
                    "FPL schema changed? (fails closed)")
        if e.get("status") == "i":
            return f"{e.get('web_name')} is now INJURED"
        if e.get("status") == "u":
            return f"{e.get('web_name')} is UNAVAILABLE (left league / gone for season)"
        cop = e.get("chance_of_playing_next_round")
        if cop is not None and cop < 50:
            return f"{e.get('web_name')} now doubtful ({cop}%)"

    # 3. price rises on transfer-ins break the plan budget
    for t in plan.get("transfers", []):
        e = els.get(t.get("element_in"))
        if e and int(e.get("now_cost", 0)) > int(t.get("purchase_price", 0)):
            return (f"{e.get('web_name')} price rose £{int(t.get('purchase_price', 0))/10:.1f}m "
                    f"-> £{int(e.get('now_cost', 0))/10:.1f}m (plan budget would break)")
    return ""


def acquire_approve_lock():
    """Take the exclusive approval lock. Returns True when held, False when busy.

    O_CREAT|O_EXCL guarantees only one caller wins. A lock file older than
    LOCK_STALE_SECONDS is treated as stale (the holder crashed) and reclaimed.
    Complements the in-flight 'executing' plan marker: the marker is the bot's
    state machine, the lock serializes concurrent approve_plan() callers.
    """
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age_sec = time.time() - os.path.getmtime(LOCK_FILE)
        except OSError:
            return False
        if age_sec <= LOCK_STALE_SECONDS:
            return False
        # stale lock (holder crashed) -> reclaim it
        try:
            os.unlink(LOCK_FILE)
        except OSError:
            return False
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"pid": os.getpid(),
                            "taken_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}))
    return True


def release_approve_lock():
    try:
        os.unlink(LOCK_FILE)
    except OSError:
        pass


def execution_enabled():
    """Whether this bot instance is explicitly allowed to submit FPL writes."""
    return (os.environ.get(EXECUTION_ENABLED_ENV) == "1"
            and os.environ.get(DRY_RUN_ENV, "1") != "1")


def execution_confirmation(uid=None):
    """Return the confirmation token bound to the current immutable plan.

    `/approve` is intentionally a preview step.  The second Telegram tap must
    carry this short token; the callback resolves it against the full plan hash
    immediately before reaching the executor.
    """
    if not authorized(uid):
        return None, "❌ Not authorized to approve plans."
    if not execution_enabled():
        return None, "❌ Execution is disabled on this bot. No FPL write was sent."
    plan = load_pending()
    if not plan:
        return None, "No pending plan to approve."
    if not _plan_is_executable(plan):
        return None, "❌ This plan is not executable. Run /simulate to generate a fresh V4 plan."
    plan_id = plan.get("plan_id")
    if not isinstance(plan_id, str) or canonical_plan_hash(plan) != plan_id:
        return None, "❌ Plan identity mismatch — run /simulate to regenerate it."
    return short_id(plan_id), (
        f"⚠️ <b>CONFIRM FPL EXECUTION</b>\n"
        f"Plan: <code>{html.escape(short_id(plan_id))}</code> • GW{plan.get('gw')}\n\n"
        "Tap <b>Execute this exact plan</b> to submit its transfers, lineup and captain to FPL. "
        "This cannot be undone here."
    )


def approve_plan(uid=None, approved_plan_id=None):
    """Approve the pending plan and execute it.

    Sol audit P0-1: REQUIRES an authorized immutable Telegram user id.
    Chat membership is NOT authorization. Fail closed for unknown callers.
    """
    if not authorized(uid):
        return "❌ Not authorized to approve plans."
    if not execution_enabled():
        return "❌ Execution is disabled on this bot. No FPL write was sent."
    # A command or old inline button never reaches the executor by itself. The
    # callback must bind this approval to the exact canonical packet it showed.
    plan = load_pending()
    plan_id = plan.get("plan_id") if plan else None
    if (not isinstance(approved_plan_id, str) or not isinstance(plan_id, str)
            or not hmac.compare_digest(approved_plan_id, plan_id)):
        return ("❌ Confirmation is missing or no longer matches the current plan. "
                "Run /approve again and confirm the displayed plan hash.")
    if not acquire_approve_lock():
        return ("❌ Another approval is already running (lock held).\n"
                "If a previous run hung, wait up to 15 minutes — the lock "
                "expires automatically (stale > 15 min).")
    try:
        return _approve_plan_locked()
    finally:
        release_approve_lock()


def _approve_plan_locked():
    plan = load_pending()
    if not plan:
        return "No pending plan to approve."
    if plan.get("status") == "executed":
        return "Plan already executed."
    if plan.get("status") == "executing":
        # F44/F45: an execution is ALREADY in flight (duplicate tap, or the bot
        # crashed mid-POST and restarted). Never start a second execution of the
        # same plan - double transfer POSTs at deadline are real -8pt+ errors.
        return ("❌ Execution already in progress or was interrupted mid-run.\n"
                "If this is a duplicate tap, ignore this message — the first tap is running.\n"
                "If the bot crashed mid-execution, run /simulate to regenerate a fresh plan, "
                "then /approve again. NEVER re-approve an 'executing' plan blindly.")
    # Sol GW1 directive W2: HARD deadline cutoff (deadline - 30 min).
    # Fail closed: invalid/naive deadline blocks approval.
    deadline = plan.get("deadline")
    settings_now = load_settings()
    cutoff_min = settings_now.get("approval_cutoff_minutes", 30)
    if is_past_cutoff(deadline, cutoff_minutes=cutoff_min):
        return ("❌ Deadline cutoff reached — approval locked.\n"
                "No execution after the deadline. The current team will be retained.")
    # Sol GW1 directive W1: proposal identity binding. The plan hash and input
    # fingerprint must still match what was generated. Any changed source or
    # decision snapshot invalidates the outstanding approval.
    stored_plan_id = plan.get("plan_id")
    if stored_plan_id:
        current_id = canonical_plan_hash(plan)
        if current_id != stored_plan_id:
            return ("❌ Plan identity mismatch — not executing.\n"
                    "The plan file changed after it was generated. "
                    "Run /simulate to regenerate, then /approve again.")
    if plan.get("status") not in ("pending", "fallback"):
        return (f"❌ Plan status is {plan.get('status', 'unknown')} — not executing.\n"
                "Run /simulate to generate a fresh pending plan.")
    if plan.get("model_version") != "competitive-v4.0" or plan.get("status") == "fallback":
        plan["status"] = "invalid"
        plan["validation_errors"] = ["legacy_engine_plan: only competitive-v4.0 packets are executable"]
        save_pending(plan)
        return ("❌ Legacy/fallback plan is diagnostic only — not executable.\n"
                "V4 must recover and /simulate must generate a canonical decision packet.")
    plan_errors = validate_plan(plan)
    if plan_errors:
        plan["status"] = "invalid"
        plan["validation_errors"] = plan_errors
        save_pending(plan)
        return ("❌ Plan is INVALID — no FPL request was sent.\n"
                + "\n".join(f"• {error}" for error in plan_errors)
                + "\nRun /simulate after the bot has been fixed.")
    # STALE-PLAN GUARD: re-validate against live data before executing.
    # A plan can go stale between generation and approval (price changes,
    # injuries, deadline passed). Blindly submitting a stale plan is the
    # single most dangerous failure mode of approval-gated execution.
    stale_reason = plan_staleness(plan)
    if stale_reason:
        return ("❌ Plan is STALE — not executing.\n"
                f"Reason: {stale_reason}\n"
                "Run /simulate to regenerate a fresh plan, then /approve again.")
    team_diff = ((plan.get("decision_summary") or {}).get("team_diff") or {})
    if (not plan.get("transfers") and not plan.get("chip")
            and team_diff.get("write_required") is False):
        plan["status"] = "acknowledged"
        plan["acknowledged_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save_pending(plan)
        return (f"✅ GW{plan.get('gw')} HOLD acknowledged.\n"
                "The live squad, lineup, captain and vice already match the plan. "
                "No FPL request was sent.")
    # F44/F45: mark execution in flight BEFORE the first POST. A duplicate tap
    # now sees status='executing' and refuses; a crash mid-POST leaves
    # 'executing' on disk so a restart can detect the interrupted execution
    # instead of silently re-running the whole plan from scratch.
    plan["status"] = "executing"
    plan["execution_started_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_pending(plan)
    # execute via executor module
    sys.path.insert(0, os.path.join(BASE, "execution"))
    from executor import execute_plan, is_success
    import io
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    execution_error = None
    try:
        r1, r2, matched = execute_plan(plan)
        ok = is_success(r1) and is_success(r2) and matched
    except InvalidPlanError as exc:
        execution_error = exc
    finally:
        sys.stdout = old
    log = buf.getvalue()
    # Keep the detailed execution log for debugging, but NEVER dump it in chat.
    try:
        log_dir = os.path.join(BASE, "data", "processed", "execution_logs")
        os.makedirs(log_dir, exist_ok=True)
        fname = os.path.join(log_dir, "exec_%s.log" %
                             datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        with open(fname, "w", encoding="utf-8") as f:
            f.write(log)
    except Exception:
        pass
    if execution_error is not None:
        plan["status"] = "invalid"
        plan["validation_errors"] = execution_error.errors
        save_pending(plan)
        return ("❌ Execution blocked before the first FPL write.\n"
                + "\n".join(f"• {error}" for error in execution_error.errors)
                + "\nRun /simulate to bind a fresh plan to the live squad.")
    # P0.1 (7 Aug audit): 2xx/202 alone means ACCEPTED, not proven applied.
    # If FPL accepted but final state did not verify, the plan must NOT be
    # marked 'executed' - it goes to verification_pending instead.
    if ok:
        plan["status"] = "executed"
    elif matched is False:
        plan["status"] = "verification_pending"
    else:
        plan["status"] = "failed"
    save_pending(plan)
    if ok:
        return execution_summary(plan)
    if matched is False:
        return ("⚠️ FPL accepted the changes (2xx) but the final squad did not "
                "match the target after polling.\n"
                "Status: verification_pending — CHECK THE SQUAD MANUALLY "
                "(tap 🛡️ Team) before doing anything else.")
    # failed: clean message with HTTP status codes - no raw blob
    codes = []
    if r1 is not None:
        codes.append(f"transfers={r1.status_code}")
    if r2 is not None:
        codes.append(f"lineup={r2.status_code}")
    detail = f" ({', '.join(codes)})" if codes else ""
    return (f"❌ Execution failed{detail} — FPL rejected the request.\n"
            "No lineup/transfer was accepted. Run 🧠 Simulate to regenerate "
            "a fresh plan, then Approve again.")


def execution_summary(plan):
    """Clean human-readable confirmation of an executed plan — NO raw log.

    Built from the plan payload only, so the chat message is always a proper
    card instead of the executor's stdout blob.
    """
    gw = plan.get("gw")
    trs = plan.get("transfers", [])
    cap = (plan.get("captain") or {}).get("name", "?")
    vice = (plan.get("vice") or {}).get("name", "—")
    chip = plan.get("chip")
    deadline = plan.get("deadline")

    lines = [f"✅ <b>GW{gw} plan executed!</b>", "━━━━━━━━━━━━━━━━━━"]
    if plan.get("plan_id"):
        lines.append(f"🧾 Plan: <code>{html.escape(short_id(plan['plan_id']))}</code>")
    if trs:
        for t in trs:
            hit = " (−4 hit)" if t.get("hit") else ""
            lines.append(f"🔄 {t.get('out_name', '?')} → {t.get('in_name', '?')}{hit}")
    else:
        reason = ((plan.get("decision_summary") or {}).get("reason")
                  or "No legal transfer cleared the configured threshold.")
        lines.append(f"🔄 Transfers: 0 — no transfer submitted. {reason}")
    lines.append(f"🛡️ Lineup: 11 starters + 4 bench submitted")
    lines.append(f"👑 Captain: {cap} (×2)")
    lines.append(f"🥈 Vice: {vice}")
    lines.append(f"🎩 Chip: {chip or 'none'}")
    if deadline:
        lines.append(f"⏰ Locks in at the GW{gw} deadline: {deadline}")
    lines.append("\n✅ Final state verified on FPL.")
    return "\n".join(lines)


def reject_plan(uid=None):
    """Sol audit P0-1: authorized immutable user id required."""
    if not authorized(uid):
        return "❌ Not authorized to reject plans."
    plan = load_pending()
    if not plan:
        return "No pending plan to reject."
    reference = short_id(plan.get("plan_id")) if plan.get("plan_id") else "unknown"
    plan["status"] = "rejected"
    save_pending(plan)
    return (f"❌ Plan {reference} rejected — squad unchanged.\n"
            "Run 🧠 Simulate when you want a fresh proposal.")


def chip_text(args, uid=None):
    """Sol audit P0-1: authorized immutable user id required to stage a chip."""
    if not authorized(uid):
        return "❌ Not authorized to stage chips."
    valid = {"wildcard": "Wildcard", "freehit": "Free Hit", "benchboost": "Bench Boost", "triplecaptain": "Triple Captain"}
    key = (args[0] if args else "").lower().replace("_", "").replace("-", "")
    if key not in valid:
        return "Chip must be one of: wildcard, freehit, benchboost, triplecaptain"
    # QA hardening: a chip may only be staged ON an existing simulated pending
    # plan. Running /chip before /simulate used to create a malformed
    # {chip: ...} - only plan that crashed /approve with KeyError 'captain'.
    plan = load_pending()
    if not plan:
        return ("❌ No pending plan to attach a chip to.\n"
                "Run /simulate first — the chip is staged ON the pending plan "
                "and sent with the next /approve.")
    if not plan.get("gw") or not plan.get("captain"):
        return ("❌ The pending plan is not a valid simulated plan (missing gw or captain).\n"
                "Run /simulate to generate a proper plan, then stage the chip again.")
    if plan.get("status") not in (None, "pending"):
        return (f"❌ Cannot stage a chip on a {plan.get('status')} plan.\n"
                "Run /simulate to generate a fresh pending plan, then stage the chip again.")
    sys.path.insert(0, os.path.join(BASE, "execution"))
    import chips
    api_code = chips.CHIP_API[valid[key]]
    # availability window check (e.g. wildcard starts GW2, not playable GW1)
    try:
        windows = chips.fetch_chip_windows()
        gw_now = next_gw_id()
        if not chips.chip_playable_in(api_code, gw_now, windows):
            hint = chips.chip_windows_hint(windows)
            return (f"❌ {valid[key]} NOT playable in GW{gw_now}. "
                    f"Available: {hint.get(api_code, '?')}.")
    except Exception:
        pass  # if window fetch fails, let the API be the judge
    plan["chip"] = api_code          # store FPL API code, not display name
    plan["chip_gw"] = plan.get("gw")
    save_pending(plan)
    try:
        _tid = load_settings().get("team_id", "?")
    except Exception:
        _tid = "?"
    return (f"🎩 {valid[key]} staged for GW{plan.get('gw')}. "
            f"It will be sent to FPL with the next /approve (endpoint: {chips.chip_endpoint(api_code, _tid)}).")


def next_gw_id():
    """Current next (unfinished) gameweek id from bootstrap."""
    try:
        d = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
        for ev in d["events"]:
            if not ev["finished"]:
                return int(ev["id"])
    except Exception:
        pass
    return 1


def history_text():
    client = FPLClient()
    settings = load_settings()
    try:
        h = client.entry_history(settings["team_id"])
    except Exception as exc:
        return f"⚠️ Could not retrieve FPL history: {html.escape(str(exc)[:160])}"
    # FPL calls current-season rows `current`; `history` is retained for old
    # cached payloads. Reading only `history` made a real GW1 record appear
    # missing throughout the first gameweek.
    hist = h.get("current") or h.get("history") or []
    hist = sorted(hist, key=lambda row: int(row.get("event") or 0))[-6:]
    if not hist:
        gw = next_gw_id()
        if gw <= 1:
            return "No GW history yet — season hasn't started."
        return f"No completed GW history returned by FPL yet (next deadline: GW{gw})."
    rows = [(str(row.get("event")), str(row.get("points")), str(row.get("total_points")), str(row.get("rank"))) for row in hist]
    latest = hist[-1]
    chip = next((row.get("name") for row in (h.get("chips") or []) if int(row.get("event") or -1) == int(latest.get("event") or 0)), None)
    detail = (
        f"\n\n🧾 <b>GW{latest.get('event')} details</b>\n"
        f"   Transfers {int(latest.get('event_transfers') or 0)} • hits −{int(latest.get('event_transfers_cost') or 0)} "
        f"• bench {int(latest.get('points_on_bench') or 0)} pts • chip {html.escape(str(chip or 'none'))}"
    )
    return history_message(rows) + detail


def league_text(state=None):
    """League monitor + manager sharpness + beat-them readout."""
    import glob as _glob
    import json as _json
    import os as _os
    sys.path.insert(0, _os.path.join(BASE, "model"))
    latest = _os.path.join(BASE, "data", "processed", "league_intelligence", "latest.json")
    if state is not None or _os.path.exists(latest):
        try:
            state = state or _json.load(open(latest, encoding="utf-8"))
            mode = state.get("mode", {}) or {}
            lines = [
                f"📊 <b>LEAGUE INTELLIGENCE — GW{state.get('event')}</b>",
                f"Mode: <b>{mode.get('mode', 'Neutral')}</b>",
                f"Deep cohort: {state.get('cohort_count', 0)} • trusted squads: {state.get('trusted_pick_count', 0)}",
                f"Registry: <b>{html.escape(str((state.get('registry') or {}).get('status', 'unknown')).upper())}</b>"
                + (f" • finalized {(state.get('registry') or {}).get('finalized_at', '')[:16]} UTC" if (state.get('registry') or {}).get('finalized_at') else " • accepting late entrants"),
                "━━━━━━━━━━━━━━━━━━",
                "🏆 <b>PRIZE TARGETS</b>",
            ]
            for target in state.get("prize_status", []) or []:
                league_label = html.escape(str(target.get("league_id")))
                if target.get("rank") is None:
                    lines.append(f"• L{league_label}: standings not live yet")
                else:
                    current = html.escape(str((target.get("current_prize") or {}).get("prize", "outside prize bands")))
                    nxt = target.get("next_target") or {}
                    text = f"• L{league_label}: rank <b>{int(target.get('rank'))}</b> • {current}"
                    if nxt:
                        text += f" → {html.escape(str(nxt.get('prize')))}"
                        if target.get("gap_to_next_target") is not None:
                            text += f" ({float(target['gap_to_next_target']):.0f} pts)"
                    lines.append(text)
                    probability = target.get("probability") or {}
                    if probability.get("available"):
                        lines.append(
                            f"  🎲 projected rank {float(probability.get('expected_rank', 0)):.0f} • "
                            f"P(top 10) {float(probability.get('p_top_10', 0)):.1f}% • "
                            f"P(top 40) {float(probability.get('p_top_40', 0)):.1f}%"
                        )
                special = target.get("active_special") or []
                if special:
                    lines.append(
                        f"  ⚡ GW{state.get('event')} special: {html.escape(str(special[0].get('prize')))} "
                        f"winner • top {special[-1].get('rank_to')} paid"
                    )
            monthly = state.get("monthly_status", []) or []
            if monthly:
                lines.extend(["", "📅 <b>MONTHLY TARGETS</b>"])
                for target in monthly[:2]:
                    prize = html.escape(str((target.get("prize") or {}).get("prize", "monthly prize")))
                    if target.get("rank") is None:
                        lines.append(f"• L{target.get('league_id')}: awaiting completed GW data • {prize}")
                    else:
                        lines.append(
                            f"• L{target.get('league_id')}: rank {int(target['rank'])} • "
                            f"{float(target.get('gap_to_first', 0)):.0f} pts to first • {prize}"
                        )
            threats = sorted(
                state.get("cohort", []) or [],
                key=lambda row: -float(row.get("live_sharpness", row.get("historical_score", 50)) or 50),
            )[:5]
            if threats:
                lines.extend(["", "🚨 <b>PRIORITY THREATS</b>"])
                for row in threats:
                    score = float(row.get("live_sharpness", row.get("historical_score", 50)) or 50)
                    name = html.escape(str(row.get("team_name") or "Unknown"))
                    tier = html.escape(str(row.get("tier", "?")))
                    lines.append(f"• {name} • {score:.1f} • tier {tier}")
            moves = state.get("transfer_consensus", []) or []
            if moves:
                lines.extend(["", "🔄 <b>SHARP TRANSFER CONSENSUS</b>"])
                for move in moves[:3]:
                    lines.append(
                        f"• {html.escape(str(move.get('name')))}: +{float(move.get('weighted_in_pct', 0)):.0f}% / "
                        f"−{float(move.get('weighted_out_pct', 0)):.0f}% weighted"
                    )
            swing = state.get("live_swing") or {}
            if swing:
                rivals = swing.get("rivals", []) or []
                best = rivals[0] if rivals else None
                text = f"⚡ <b>Live cohort:</b> us {float(swing.get('our_live_points', 0)):.0f} pts"
                if best:
                    text += f" • leader {float(best.get('live_points', 0)):.0f} ({float(best.get('swing_vs_us', 0)):+.0f})"
                lines.extend(["", text])
            market = state.get("market_signals", []) or []
            if market:
                actionable = sum(1 for row in market if _market_direction(row) in {"rise", "fall"} or row.get("chance_next") is not None)
                lines.extend(["", f"💷 <b>Market:</b> {actionable} actionable signals • open Market Watch for details"])
            return _safe_card(lines)
        except Exception:
            pass  # fall back to the legacy snapshot below

    gw = next_gw_id() - 1 or 1
    # find latest monitor snapshot
    snaps = sorted(_glob.glob(_os.path.join(BASE, "data", "processed", "league_monitor_gw*.json")))
    if not snaps:
        return ("📊 <b>LEAGUE MONITOR</b>\n"
                "No snapshot yet — standings populate after GW1. "
                "I'll start tracking opponents live from the first deadline.")
    snap = _json.load(open(snaps[-1], encoding="utf-8"))
    lines = [f"📊 <b>LEAGUE MONITOR — GW{snap.get('gw')}</b>",
             f"Entries tracked: {snap.get('standings_count', 0)}",
             "━━━━━━━━━━━━━━━━━━"]
    # our position
    our = snap.get("entries", {}).get(str(snap.get("our_entry")), {})
    if our:
        lines.append(f"🏠 <b>{our.get('entry_name', 'US')}</b> — rank {our.get('rank', '?')} · {our.get('total', 0)} pts")
    # sharp managers: use manager histories if available; else show threats from snapshot
    threats = []
    for eid, entry in (snap.get("entries") or {}).items():
        gap = entry.get("gap_to_us")
        if gap is not None and abs(gap) <= 20:
            threats.append(f"⚠️ {entry.get('entry_name')} — {gap:+.0f} pts vs us")
    if threats:
        lines.append("")
        lines.append("🚨 <b>THREATS</b>")
        lines.extend(threats)
    # captain live leaders
    cap_leaders = sorted(
        [(e.get("captain_live_pts", 0), e.get("entry_name"), e.get("captain_name"))
         for e in (snap.get("entries") or {}).values() if e.get("captain_live_pts")],
        reverse=True)[:5]
    if cap_leaders:
        lines.append("")
        lines.append("👑 <b>CAPTAIN LIVE</b>")
        for pts, name, cap in cap_leaders:
            lines.append(f"   {name}: {cap} on {pts} pts")
    return _safe_card(lines)


def load_league_state():
    """Load the latest league snapshot, or None on corrupt/missing data."""
    try:
        with open(LEAGUE_STATE_FILE, encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _league_state_or_message(state):
    state = state if state is not None else load_league_state()
    if not state:
        return None, (
            "🏆 <b>LEAGUE WAR ROOM</b>\n"
            "No intelligence snapshot yet. Tap <b>Refresh Data</b>; it only "
            "reads public FPL endpoints and cannot change your team."
        )
    return state, None


def _safe_number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_card(lines, limit=4096):
    """Join complete HTML lines without cutting a tag at Telegram's limit."""
    output = []
    size = 0
    footer = "\n<i>More rows hidden to keep this card mobile-safe.</i>"
    for raw in lines:
        line = str(raw)
        addition = len(line) + (1 if output else 0)
        if size + addition + len(footer) > limit:
            output.append(footer.lstrip("\n"))
            break
        output.append(line)
        size += addition
    return "\n".join(output)


def _error_card(area):
    return (
        f"⚠️ <b>{html.escape(str(area))} unavailable</b>\n"
        "The live data could not be loaded safely. Refresh and try again; no FPL write was attempted."
    )


def _plan_is_executable(plan):
    plan = plan or {}
    competitive = plan.get("competitive") or {}
    source_status = (((plan.get("decision_summary") or {}).get("source_manifest") or {}).get("status"))
    transfer_safe = not plan.get("transfers") or source_status == "ready"
    context_safe = not competitive.get("fallback") and competitive.get("context_status") != "pending"
    return bool(
        plan.get("status") == "pending"
        and plan.get("model_version") == "competitive-v4.0"
        and plan.get("plan_id") and plan.get("input_fp")
        and transfer_safe and context_safe
    )


def _owned_player_ids():
    plan = load_pending() or {}
    ids = {int(value) for value in (plan.get("pre_transfer_squad_ids") or [])}
    for row in (plan.get("target_starters") or []) + (plan.get("bench") or []):
        if row.get("id") is not None:
            ids.add(int(row["id"]))
    return ids


def _market_direction(row):
    projection = row.get("projection") if isinstance(row.get("projection"), dict) else {}
    return str(projection.get("direction") or "monitor").lower()


def _market_priority(row, owned):
    chance = row.get("chance_next")
    chance_value = 100 if chance is None else _safe_number(chance, 100)
    direction = _market_direction(row)
    is_owned = int(row.get("element") or -1) in owned
    return (
        0 if is_owned and chance_value < 75 else
        1 if chance_value < 75 else
        2 if is_owned and direction == "fall" else
        3 if direction == "fall" else
        4 if direction == "rise" else 5,
        chance_value,
        -abs(_safe_number(row.get("net_transfers_event"))),
    )


def _market_action(row, owned):
    element = int(row.get("element") or -1)
    is_owned = element in owned
    chance = row.get("chance_next")
    chance_value = 100 if chance is None else _safe_number(chance, 100)
    direction = _market_direction(row)
    if chance_value < 75:
        return "Bench/replace check" if is_owned else "Avoid buying"
    if direction == "fall":
        return "Review sale" if is_owned else "Avoid buying"
    if direction == "rise":
        return "Monitor before price update"
    return "Monitor"


def _market_card(state, section="market"):
    event = state.get("event", "?")
    owned = _owned_player_ids()
    unique = {}
    for row in state.get("market_signals") or []:
        key = int(row.get("element") or hash(str(row.get("name"))))
        unique.setdefault(key, row)
    rows = list(unique.values())
    if section == "market_fall":
        rows = [row for row in rows if _market_direction(row) == "fall"]
        title = f"🔻 <b>PRICE-FALL RISKS — GW{event}</b>"
    elif section == "market_rise":
        rows = [row for row in rows if _market_direction(row) == "rise"]
        title = f"🔺 <b>PRICE-RISE WATCH — GW{event}</b>"
    elif section == "market_availability":
        rows = [row for row in rows if row.get("chance_next") is not None and _safe_number(row.get("chance_next"), 100) < 100]
        title = f"🚑 <b>AVAILABILITY — GW{event}</b>"
    else:
        title = f"💷 <b>MARKET WATCH — GW{event}</b>"
    rows.sort(key=lambda row: _market_priority(row, owned))
    rows = rows[:6] if section == "market" else rows[:12]
    lines = [title, "━━━━━━━━━━━━━━━━━━"]
    current_group = None
    for row in rows:
        direction = _market_direction(row)
        chance = row.get("chance_next")
        chance_value = 100 if chance is None else _safe_number(chance, 100)
        group = "availability" if chance_value < 100 else direction
        if section == "market" and group != current_group:
            label = {"availability": "🚑 <b>AVAILABILITY CONCERNS</b>", "fall": "🔻 <b>PRICE-FALL RISK</b>",
                     "rise": "🔺 <b>PRICE-RISE WATCH</b>"}.get(group, "ℹ️ <b>MONITOR</b>")
            lines.extend(["", label])
            current_group = group
        icon = "🚑" if chance_value < 100 else ("🔻" if direction == "fall" else "🔺" if direction == "rise" else "ℹ️")
        play = f" • {chance_value:.0f}% play" if chance is not None else ""
        owned_badge = " • 🏠 owned" if int(row.get("element") or -1) in owned else ""
        lines.append(
            f"{icon} <b>{html.escape(str(row.get('name') or row.get('element')))}</b> "
            f"• £{_safe_number(row.get('now_cost')):.1f}m{play}{owned_badge}\n"
            f"   ↳ {html.escape(_market_action(row, owned))}"
        )
    if not rows:
        lines.append("🟢 No material signal in this category right now.")
    as_of = html.escape(str(state.get("as_of") or "unknown")[:19].replace("T", " "))
    lines.extend(["", f"<i>Updated {as_of} UTC • directional signal, not a guaranteed price move.</i>",
                  "<i>Approval re-checks live price and availability before any FPL write.</i>"])
    return _safe_card(lines)


def _projection_label(value):
    """Collapse the API's multi-hour projection payload into one short label."""
    if isinstance(value, list):
        best = max(value, key=lambda row: _safe_number((row or {}).get("likelihood")), default=None)
        if best:
            pct = best.get("projected_percent")
            likelihood = _safe_number(best.get("likelihood"))
            if pct not in (None, "") and likelihood:
                return f"{pct}% ({likelihood:.0f}% confidence)"
        return "no projected move"
    return str(value or "no projected move")


def war_room_text(section="overview", state=None):
    """Render a compact advisory card; never mutate the user's FPL team."""
    state, missing = _league_state_or_message(state)
    if missing:
        return missing
    event = state.get("event", "?")
    mode = state.get("mode") or {}
    registry = state.get("registry") or {}
    as_of = html.escape(str(state.get("as_of") or "unknown")[:19].replace("T", " "))

    if section == "overview":
        return league_text(state)

    if section == "prize":
        lines = [f"🎯 <b>PRIZE RACE — GW{event}</b>", f"Strategy: <b>{html.escape(str(mode.get('mode', 'Neutral')))}</b>"]
        for row in state.get("prize_status", []) or []:
            lid = html.escape(str(row.get("league_id")))
            if row.get("rank") is None:
                lines.append(f"\n<b>L{lid}</b> • standings start after GW1")
            else:
                current = html.escape(str((row.get("current_prize") or {}).get("prize") or "outside prize bands"))
                lines.append(f"\n<b>L{lid}</b> • rank {int(row['rank'])} • {current}")
                target = row.get("next_target") or {}
                if target:
                    lines.append(
                        f"Next: {html.escape(str(target.get('prize')))} • "
                        f"gap {_safe_number(row.get('gap_to_next_target')):.0f} pts"
                    )
                if row.get("drop_buffer") is not None:
                    lines.append(f"Safety buffer: {_safe_number(row.get('drop_buffer')):.0f} pts")
                probability = row.get("probability") or {}
                if probability.get("available"):
                    lines.append(
                        f"Monte Carlo: expected #{_safe_number(probability.get('expected_rank')):.0f} • "
                        f"top 10 {_safe_number(probability.get('p_top_10')):.1f}% • "
                        f"top 40 {_safe_number(probability.get('p_top_40')):.1f}%"
                    )
            special = row.get("active_special") or []
            if special:
                top = html.escape(str(special[0].get("prize")))
                lines.append(f"⚡ GW{event} special: winner {top}; top {special[-1].get('rank_to')} paid")
        for row in (state.get("monthly_status") or [])[:2]:
            prize = html.escape(str((row.get("prize") or {}).get("prize") or "monthly prize"))
            rank = "awaiting scores" if row.get("rank") is None else f"rank {int(row['rank'])}, {_safe_number(row.get('gap_to_first')):.0f} pts to first"
            lines.append(f"📅 L{row.get('league_id')}: {rank} • {prize}")
        lines.append(f"\n<i>Risk model is directional, not a guarantee • updated {as_of} UTC</i>")
        return _safe_card(lines)

    if section == "rivals":
        rivals = sorted(
            state.get("cohort") or [],
            key=lambda row: -_safe_number(row.get("live_sharpness", row.get("historical_score", 50)), 50),
        )
        lines = [f"🕵️ <b>SHARP RIVALS — GW{event}</b>", f"Deep-scout cohort: {len(rivals)} managers"]
        for index, row in enumerate(rivals[:10], 1):
            score = _safe_number(row.get("live_sharpness", row.get("historical_score", 50)), 50)
            activity = row.get("activity") or {}
            lines.append(
                f"{index}. <b>{html.escape(str(row.get('team_name') or 'Unknown'))}</b> • "
                f"{score:.1f} {html.escape(str(row.get('tier') or '?'))} • "
                f"{html.escape(str(activity.get('archetype_live') or row.get('archetype') or 'unclassified'))}"
            )
        if not rivals:
            lines.append("No rival cohort available yet.")
        lines.append("\n<i>Scores combine previous-season evidence with live behaviour; they are not copied blindly.</i>")
        return _safe_card(lines)

    if section == "transfers":
        moves = state.get("transfer_consensus") or []
        lines = [f"🔄 <b>SHARP TRANSFER RADAR — GW{state.get('exposure_event') or event}</b>"]
        if not moves:
            lines.extend([
                "Locked rival transfers are not trustworthy yet.",
                f"Trusted squads: {state.get('trusted_pick_count', 0)}/{state.get('cohort_count', 0)}.",
                "This unlocks only after the deadline and two identical pick reads.",
            ])
        for row in moves[:12]:
            net = _safe_number(row.get("weighted_in_pct")) - _safe_number(row.get("weighted_out_pct"))
            lines.append(
                f"• <b>{html.escape(str(row.get('name') or row.get('element')))}</b> • "
                f"in {_safe_number(row.get('weighted_in_pct')):.1f}% / out {_safe_number(row.get('weighted_out_pct')):.1f}% "
                f"(net {net:+.1f})"
            )
        lines.append("\n<i>Consensus is evidence, never an automatic transfer instruction.</i>")
        return _safe_card(lines)

    if section == "captain":
        exposure = sorted(
            (state.get("player_exposure") or {}).values(),
            key=lambda row: (-_safe_number(row.get("captain_share")), -_safe_number(row.get("effective_ownership"))),
        )
        lines = [f"👑 <b>CAPTAIN BATTLE — GW{state.get('exposure_event') or event}</b>"]
        pending = load_pending() or {}
        captain = pending.get("captain") or {}
        if captain:
            lines.append(
                f"Our pending captain: <b>{html.escape(str(captain.get('name') or captain.get('id')))}</b> "
                f"({_safe_number(captain.get('xpts')):.1f} xPts)"
            )
        if not exposure:
            lines.extend([
                "No trusted locked captain sample yet.",
                f"Trusted squads: {state.get('trusted_pick_count', 0)}/{state.get('cohort_count', 0)}.",
            ])
        for row in exposure[:10]:
            lines.append(
                f"• <b>{html.escape(str(row.get('name') or row.get('element')))}</b> • "
                f"captain {_safe_number(row.get('captain_share')):.1f}% • "
                f"EO {_safe_number(row.get('effective_ownership')):.1f}% • "
                f"owned {_safe_number(row.get('ownership')):.1f}%"
            )
        lines.append(f"\nMode <b>{html.escape(str(mode.get('mode', 'Neutral')))}</b>: adjustment stays inside xPts guardrails.")
        return _safe_card(lines)

    if section in {"market", "market_fall", "market_rise", "market_availability"}:
        return _market_card(state, section)

    if section == "registry":
        status = html.escape(str(registry.get("status") or "unknown").upper())
        lines = [f"🔒 <b>DEADLINE REGISTRY — GW{event}</b>", f"Status: <b>{status}</b>"]
        for league in state.get("leagues") or []:
            complete = "✅ complete" if league.get("complete") else "⚠️ partial"
            lines.append(
                f"• L{league.get('league_id')} • {int(league.get('member_count', 0)):,} managers • "
                f"{league.get('pages', 0)} pages • {complete}"
            )
        digest = str(registry.get("membership_hash") or "")
        if digest:
            lines.append(f"Membership fingerprint: <code>{html.escape(digest[:12])}</code>")
        if registry.get("finalized_at"):
            lines.append(f"Finalized: {html.escape(str(registry['finalized_at'])[:19])} UTC")
        else:
            lines.append("Late entrants are still accepted and re-read on every scheduled scan.")
            lines.append("After deadline, membership freezes and locked picks are validated twice.")
        lines.append(f"Snapshot: {as_of} UTC")
        return _safe_card(lines)

    if section == "attack":
        strategy = html.escape(str(mode.get("mode") or "Neutral"))
        reason = html.escape(str(mode.get("reason") or "No live prize gap yet."))
        lines = [f"⚔️ <b>ATTACK PLAN — GW{event}</b>", f"Current posture: <b>{strategy}</b>", f"Why: {reason}"]
        reference = mode.get("reference_rival") or {}
        if reference:
            lines.append(
                f"Reference rival: {html.escape(str(reference.get('team_name') or reference.get('entry')))} "
                f"({_safe_number(reference.get('gap')):+.0f} pts vs us)"
            )
        if strategy == "Protect":
            lines.extend([
                "1. Keep the highest-xPts legal plan.",
                "2. Cover dangerous sharp-manager captain EO only within the 0.5 xPts guardrail.",
                "3. Avoid unnecessary hits; defend the current prize band and buffer.",
            ])
        elif strategy == "Chase":
            lines.extend([
                "1. Keep the highest-xPts legal transfer core.",
                "2. Seek lower-owned captain upside only within the 1.0 xPts guardrail.",
                "3. Spend variance where it can cross a prize band, not on random differentials.",
            ])
        else:
            lines.extend([
                "1. Optimize expected points; it is too early for forced differentiation.",
                "2. Use sharp-manager moves as evidence, not instructions.",
                "3. Preserve flexibility and wait for trusted post-deadline squads.",
            ])
        lines.append("\n🛡️ <b>Safety:</b> this card cannot execute transfers. /simulate builds a fresh plan; only your Approve can submit it.")
        return _safe_card(lines)

    return war_room_text("overview", state)


def refresh_league_intelligence():
    """Refresh public intelligence in a subprocess; never mutate the FPL squad."""
    if not _LEAGUE_REFRESH_LOCK.acquire(blocking=False):
        return False, "A league refresh is already running. Please wait for its result."
    command = [PYTHON, os.path.join(BASE, "jobs", "league_intelligence.py"), "--notifications-disabled"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "Refresh timed out after 5 minutes; the scheduled runner will retry."
    except OSError as exc:
        return False, f"Could not start refresh: {repr(exc)[:160]}"
    finally:
        _LEAGUE_REFRESH_LOCK.release()
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error")[-600:]
        return False, html.escape(detail)
    state = load_league_state()
    if not state:
        return False, "Refresh finished but no valid snapshot was written."
    members = sum(int(row.get("member_count", 0) or 0) for row in (state.get("leagues") or []))
    return True, (
        f"✅ Refreshed GW{state.get('event')} • {members:,} league memberships • "
        f"{state.get('cohort_count', 0)} deep rivals • registry "
        f"{(state.get('registry') or {}).get('status', 'unknown')}"
    )


def haaland_decision_text():
    """One-card Haaland decision aid (Sol W5) from the production comparison.

    Renders ONLY the current production comparison; marks wildcard-path and
    price-sensitivity as NOT assessed. Deduplicated by comparing the report
    fingerprint (callers should not post twice for an unchanged report).
    """
    import json as _json
    import os as _os
    sys.path.insert(0, os.path.join(BASE, "model"))
    from plan_context import haaland_eo_line
    comp_path = _os.path.join(BASE, "reports", "haaland_production_comparison.json")
    if not _os.path.exists(comp_path):
        return ("🎯 <b>HAALAND DECISION</b>\n"
                "No production comparison yet — run the production comparison first.")
    try:
        comp = _json.load(open(comp_path, encoding="utf-8"))
    except Exception:
        return "🎯 <b>HAALAND DECISION</b>\nComparison file unreadable."
    no_h = comp.get("no_haaland", {})
    h = comp.get("forced_haaland", {})
    if not no_h or not h:
        return "🎯 <b>HAALAND DECISION</b>\nComparison incomplete."
    xi_diff = round(no_h.get("xi_with_captain", 0) - h.get("xi_with_captain", 0), 2)
    hor_diff = round(no_h.get("squad_horizon", 0) - h.get("squad_horizon", 0), 2)
    lines = [
        "🎯 <b>HAALAND DECISION — GW1</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"<b>Without Haaland</b> (C: {no_h.get('captain')})",
        f"   XI + C: <b>{no_h.get('xi_with_captain')}</b> | 3-GW horizon: {no_h.get('squad_horizon')}",
        f"   XI: {', '.join((no_h.get('starters') or [])[:6])}…",
        "",
        f"<b>With Haaland</b> (C: {h.get('captain')})",
        f"   XI + C: <b>{h.get('xi_with_captain')}</b> | 3-GW horizon: {h.get('squad_horizon')}",
        f"   XI: {', '.join((h.get('starters') or [])[:6])}…",
        "",
        f"Modeled delta: no-Haaland <b>{'+' if xi_diff >= 0 else ''}{xi_diff}</b> pts GW1 · "
        f"<b>{'+' if hor_diff >= 0 else ''}{hor_diff}</b> over 3 GWs",
        "⚠️ Model MAE 1.43 — these margins are within model uncertainty.",
        "⚠️ <b>NOT assessed:</b> wildcard-path timing, price-change sensitivity.",
        "",
        "🚨 75% of managers own Haaland. If he hauls vs BOU, that's a 10-15 pt "
        "swing against you from most of the league in one GW.",
    ]
    try:
        eo_line = haaland_eo_line()
        if eo_line:
            # replace the static 75% line with the measured EO line
            lines = [l for l in lines if not l.startswith("🚨 75%")]
            lines.append(f"🚨 {eo_line} — if he hauls vs BOU, that's a 10-15 pt "
                         "swing against you from most of the league in one GW.")
    except Exception:
        pass  # fail-soft: keep static line
    return "\n".join(lines)


def main():
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

    # --- Singleton ownership (Sol audit P0-3) -------------------------------
    # A second bot instance polling the same token causes Telegram HTTP 409
    # conflicts and dropped updates. Take a stdlib lock BEFORE anything else;
    # the losing instance exits immediately instead of entering a conflict
    # loop. This replaces kill-by-basename as the permanent guard.
    _singleton_fd = None
    try:
        _singleton_fd = open(os.path.join(BASE, "data", "bot_singleton.lock"), "a+")
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(_singleton_fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_singleton_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("❌ Another FPL bot instance is already running (singleton lock held). Exiting.")
        sys.exit(0)
    except Exception as e:
        print(f"⚠️ singleton lock unavailable ({repr(e)[:80]}) - continuing without lock")

    # heartbeat thread so the watchdog can verify liveness
    def _heartbeat():
        while True:
            try:
                with open(HEARTBEAT_FILE, "w") as f:
                    f.write(datetime.datetime.now().isoformat())
            except Exception:
                pass
            time.sleep(60)

    import time
    import threading
    threading.Thread(target=_heartbeat, daemon=True).start()

    settings = load_settings()
    creds = load_creds()
    token = creds.get("TELEGRAM_BOT_TOKEN")
    # Allowed CHATS: the configured group + the owner's private DM.
    # Private chat with the bot has chat.id == user.id, so the owner's
    # user ID (1111111111) is also a valid chat. Authorization for
    # mutations (Approve/Reject/chips/v2 gate) is still enforced separately
    # by allowed_user_ids — chat allowlist is delivery scope, not auth.
    allowed = {settings["telegram"].get("chat_id")}
    owner_uid = settings.get("telegram", {}).get("allowed_user_ids") or []
    allowed.update(int(u) for u in owner_uid if u)
    me = settings.get("telegram", {}).get("bot_username", "@Fplnaf_bot")
    me_lower = me.lower().lstrip("@")

    def is_addressed(update: Update) -> bool:
        """In groups, only act on commands addressed to THIS bot (@Fplnaf_bot /cmd),
        so plain /start /status don't collide with the group's other bot (Hermes).
        DMs and button callbacks are always ours."""
        chat = update.effective_chat
        if chat is None:
            return False
        if chat.type == "private":
            return True
        text = (update.message.text if update.message else "") or ""
        return f"@{me_lower}" in text.lower() or me_lower in text.lower()

    async def guard(update: Update) -> bool:
        chat = update.effective_chat.id if update.effective_chat else None
        if chat not in allowed:
            await update.message.reply_text(
                "This bot is configured for the owner's group and DM only."
            )
            return False
        return True

    # Main menu as a REPLY keyboard (docked above the input bar, persistent).
    # Tapping a button sends its label as a normal text message, so the text
    # router below maps labels back to actions. Inline keyboards are kept only
    # for contextual flows (chip submenu, Keep/Exclude pickers, card approve).
    MENU_LABELS = {
        "📊 Status": "menu_status",
        "🛡️ Team": "menu_team",
        "🧠 Simulate": "menu_simulate",
        "📜 History": "menu_history",
        "✅ Approve": "menu_approve",
        "❌ Reject": "menu_reject",
        "⭐ Keep": "menu_keep",
        "⛔ Exclude": "menu_exclude",
        "🎩 Chip": "menu_chip",
        "🏆 League War Room": "menu_league",
        "❓ Help": "menu_help",
    }

    def main_kb():
        """Main navigation menu - docked reply keyboard (input-bar style)."""
        labels = list(MENU_LABELS.keys())
        rows = [labels[0:2], labels[2:4], labels[4:6], labels[6:8], labels[8:10], labels[10:11]]
        return ReplyKeyboardMarkup(
            rows,
            resize_keyboard=True,
            input_field_placeholder="FPL Autopilot menu",
        )

    async def prefs_kb(action):
        """Player picker for Keep/Exclude - one button per squad player."""
        try:
            client = FPLClient()
            bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
            els = {e["id"]: e for e in bootstrap["elements"]}
            team = client.my_team(load_settings()["team_id"])
            prefs = load_player_prefs()
            cur = set(prefs.get(action, []))
            rows = []
            for p in sorted(team.get("picks", []), key=lambda x: x["position"]):
                e = els.get(p["element"])
                if not e:
                    continue
                mark = "✅ " if p["element"] in cur else ""
                rows.append([InlineKeyboardButton(
                    f"{mark}{e['web_name']} ({POS_MAP.get(e['element_type'], '?')})",
                    callback_data=f"{action}_{p['element']}")])
            rows.append([InlineKeyboardButton("← Back", callback_data="menu_main")])
            return InlineKeyboardMarkup(rows)
        except Exception as e:
            return None

    def chip_kb():
        """Chip sub-menu."""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🎩 Wildcard", callback_data="chip_wildcard"),
             InlineKeyboardButton("🔄 Free Hit", callback_data="chip_freehit")],
            [InlineKeyboardButton("🛋️ Bench Boost", callback_data="chip_benchboost"),
             InlineKeyboardButton("👑 Triple Captain", callback_data="chip_triplecaptain")],
            [InlineKeyboardButton("🧹 Clear staged chip", callback_data="chip_clear")],
            [InlineKeyboardButton("← Back", callback_data="menu_main")],
        ])

    def war_room_kb():
        """Tactical league views; refresh is read-only and runs off-loop."""
        buttons = [InlineKeyboardButton(label, callback_data=callback)
                   for label, callback in WAR_ROOM_SECTIONS]
        rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="menu_main")])
        return InlineKeyboardMarkup(rows)

    def market_kb():
        buttons = [InlineKeyboardButton(label, callback_data=callback) for label, callback in MARKET_SECTIONS]
        return InlineKeyboardMarkup([
            buttons[:2],
            buttons[2:],
            [InlineKeyboardButton("⬅️ War Room", callback_data="menu_league")],
        ])

    def plan_action_kb():
        plan = load_pending() or {}
        if _plan_is_executable(plan):
            action = ((((plan.get("decision_summary") or {}).get("team_diff") or {})
                       .get("approval_action")) or "APPROVE")
            label = {
                "ACKNOWLEDGE": "✅ Review hold",
                "APPLY TEAM SHEET": "✅ Review team sheet",
                "APPROVE TRANSFER + TEAM SHEET": "✅ Review execution",
            }.get(action, "✅ Review execution")
            return InlineKeyboardMarkup([[
                InlineKeyboardButton(label, callback_data="menu_approve"),
                InlineKeyboardButton("❌ Reject", callback_data="menu_reject"),
            ]])
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="menu_simulate"),
            InlineKeyboardButton("❌ Reject", callback_data="menu_reject"),
        ]])

    async def simulate_and_reply(reply):
        progress = await reply(
            "⏳ <b>REFRESHING V4 DECISION</b>\n"
            "1️⃣ Official FPL data\n2️⃣ League intelligence\n3️⃣ Shared snapshot\n4️⃣ Optimization\n5️⃣ Validation",
            parse_mode="HTML",
        )
        text = await asyncio.to_thread(run_pipeline)
        try:
            await progress.edit_text(text, reply_markup=plan_action_kb(), parse_mode="HTML")
        except Exception:
            await reply(text, reply_markup=plan_action_kb(), parse_mode="HTML")

    async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        await update.message.reply_text(
            f"🟢 FPL Autopilot online — team {settings['team_id']}.\n\n"
            "How it works:\n"
            "• Before each GW deadline I run the pipeline (data → xPts → transfers → lineup) and post a plan card\n"
            "• Tap ✅ Approve and I execute transfers + captain automatically\n"
            "• Tap ❌ Reject and nothing changes\n\n"
            "Menu buttons are docked above your keyboard — Semua action guna button, tak perlu type.",
            reply_markup=main_kb(),
        )

    async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        await update.message.reply_text(
            "📊 Status — squad, bank, transfers, deadline\n"
            "🛡️ Team — XI + bench with xPts\n"
            "🧠 Simulate — run the full pipeline now\n"
            "✅ Approve — execute the pending plan\n"
            "❌ Reject — discard the pending plan\n"
            "🎩 Chip — stage a chip (wildcard/freehit/benchboost/triplecaptain)\n"
            "📜 History — last 6 GWs results\n"
            "🏆 League War Room — prize race, sharp rivals, transfers, captains, market and attack plan\n\n"
            "Semua boleh ditekan terus dari menu.",
            reply_markup=main_kb(),
        )

    async def cmd_requestleague(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        if not ctx.args or not ctx.args[0].isdigit():
            await update.message.reply_text("Usage: /requestleague <league_id> [friendly name]", reply_markup=main_kb())
            return
        league_id = int(ctx.args[0])
        friendly = " ".join(ctx.args[1:]).strip() or None
        await update.message.reply_text(request_league(update.effective_user.id, league_id, friendly), parse_mode="HTML", reply_markup=main_kb())

    async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        try:
            await update.message.reply_text(status_text(), parse_mode="HTML", reply_markup=main_kb())
        except Exception as e:
            await update.message.reply_text(_error_card("Status"), parse_mode="HTML", reply_markup=main_kb())

    async def cmd_team(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        try:
            await update.message.reply_text(team_text(), parse_mode="HTML", reply_markup=main_kb())
        except Exception as e:
            await update.message.reply_text(_error_card("Team"), parse_mode="HTML", reply_markup=main_kb())

    async def cmd_simulate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        await simulate_and_reply(update.message.reply_text)

    async def cmd_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        token, message = execution_confirmation(update.effective_user.id)
        if token:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("⚠️ Execute this exact plan", callback_data=f"execute:{token}"),
                InlineKeyboardButton("Cancel", callback_data="menu_main"),
            ]])
            await update.message.reply_text(message, parse_mode="HTML", reply_markup=keyboard)
        else:
            await update.message.reply_text(message, reply_markup=main_kb())

    async def cmd_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        await update.message.reply_text(reject_plan(update.effective_user.id), reply_markup=main_kb())

    async def cmd_haaland(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """One-card Haaland decision aid from the production comparison."""
        if not await guard(update):
            return
        await update.message.reply_text(haaland_decision_text(), parse_mode="HTML", reply_markup=main_kb())

    async def cmd_chip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        await update.message.reply_text("🎩 Pilih chip:", reply_markup=chip_kb())

    async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        try:
            await update.message.reply_text(history_text(), parse_mode="HTML", reply_markup=main_kb())
        except Exception as e:
            await update.message.reply_text(_error_card("History"), parse_mode="HTML", reply_markup=main_kb())

    async def cmd_league(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        try:
            await update.message.reply_text(war_room_text(), parse_mode="HTML", reply_markup=war_room_kb())
        except Exception as e:
            await update.message.reply_text(_error_card("League War Room"), parse_mode="HTML", reply_markup=main_kb())

    async def cmd_whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Report the caller's immutable Telegram user id (bootstrap aid for
        setting allowed_user_ids). Read-only; no authorization required."""
        if not await guard(update):
            return
        uid = update.effective_user.id if update.effective_user else None
        record_user_id(uid)
        await update.message.reply_text(
            f"👤 Your Telegram user id: <code>{uid}</code>\n\n"
            "If this is you (the owner), add this number to "
            "<code>config/settings.json → telegram.allowed_user_ids</code> "
            "to unlock approve/reject/chip controls.",
            parse_mode="HTML", reply_markup=main_kb())

    async def dispatch_menu(data, reply, uid=None):
        """Execute a menu action; `reply` is the message reply callable
        (query.message.reply_text for inline taps, update.message.reply_text
        for docked reply-keyboard taps). uid = immutable caller user id,
        required for privileged branches (approve/reject/chip/keep/exclude)."""
        if data == "menu_status":
            try:
                await reply(status_text(), parse_mode="HTML", reply_markup=main_kb())
            except Exception as e:
                await reply(_error_card("Status"), parse_mode="HTML", reply_markup=main_kb())
        elif data == "menu_team":
            try:
                await reply(team_text(), parse_mode="HTML", reply_markup=main_kb())
            except Exception as e:
                await reply(_error_card("Team"), parse_mode="HTML", reply_markup=main_kb())
        elif data == "menu_simulate":
            await simulate_and_reply(reply)
        elif data == "menu_approve":
            token, message = execution_confirmation(uid)
            if token:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("⚠️ Execute this exact plan", callback_data=f"execute:{token}"),
                    InlineKeyboardButton("Cancel", callback_data="menu_main"),
                ]])
                await reply(message, parse_mode="HTML", reply_markup=keyboard)
            else:
                await reply(message, reply_markup=main_kb())
        elif data == "menu_reject":
            await reply(reject_plan(uid), reply_markup=main_kb())
        elif data == "menu_chip":
            if not authorized(uid):
                await reply("❌ Not authorized to stage chips.", reply_markup=main_kb())
            else:
                await reply("🎩 Pilih chip:", reply_markup=chip_kb())
        elif data == "menu_history":
            try:
                await reply(history_text(), parse_mode="HTML", reply_markup=main_kb())
            except Exception as e:
                await reply(_error_card("History"), parse_mode="HTML", reply_markup=main_kb())
        elif data == "menu_league":
            try:
                await reply(war_room_text(), parse_mode="HTML", reply_markup=war_room_kb())
            except Exception as e:
                await reply(_error_card("League War Room"), parse_mode="HTML", reply_markup=main_kb())
        elif data == "menu_help":
            await reply(
                "📊 Status — squad, bank, transfers, deadline\n"
                "🛡️ Team — XI + bench with xPts\n"
                "🧠 Simulate — run the full pipeline now\n"
                "✅ Approve — execute the pending plan\n"
                "❌ Reject — discard the pending plan\n"
                "🎩 Chip — stage a chip (wildcard/freehit/benchboost/triplecaptain)\n"
                "📜 History — last 6 GWs results\n"
                "🏆 League War Room — prize race, sharp rivals, transfers, captains, market and attack plan",
                reply_markup=main_kb(),
            )
        elif data == "menu_main":
            await reply("🏠 Menu utama:", reply_markup=main_kb())
        elif data in ("menu_keep", "menu_exclude"):
            if not authorized(uid):
                await reply("❌ Not authorized to edit keep/exclude prefs.", reply_markup=main_kb())
                return
            action = data.split("_")[1]
            kb = await prefs_kb(action)
            if kb is None:
                await reply("❌ Could not load squad - try again.", reply_markup=main_kb())
            else:
                label = "⭐ KEEP" if action == "keep" else "⛔ EXCLUDE"
                await reply(
                    f"{label} — tap a player to toggle:\n\n"
                    "• Keep = solver will NEVER sell this player\n"
                    "• Exclude = solver never brings them in and won't start them",
                    reply_markup=kb)

    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Router for docked reply-keyboard taps (they arrive as text)."""
        if update.effective_chat is None or update.effective_chat.id not in allowed:
            return
        text = (update.message.text or "").strip()
        data = MENU_LABELS.get(text)
        if not data:
            return  # not a menu label - ignore
        uid = update.effective_user.id if update.effective_user else None
        await dispatch_menu(data, update.message.reply_text, uid=uid)

    async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if query.message.chat.id not in allowed:
            await query.answer("Not authorized.")
            return
        await query.answer()
        data = query.data
        uid = query.from_user.id if query.from_user else None

        if data == "menu_main":
            await query.message.reply_text("🏠 Menu utama:", reply_markup=main_kb())
        elif data == "war_refresh":
            await query.message.reply_text(
                "🔄 Refreshing all league pages and rival intelligence in the background…"
            )
            ok, detail = await asyncio.to_thread(refresh_league_intelligence)
            prefix = "" if ok else "❌ "
            await query.message.reply_text(
                prefix + detail,
                parse_mode="HTML",
                reply_markup=war_room_kb(),
            )
        elif data.startswith("war_"):
            section = data.replace("war_", "", 1)
            await query.message.reply_text(
                war_room_text(section),
                parse_mode="HTML",
                reply_markup=market_kb() if section.startswith("market") else war_room_kb(),
            )
        elif data.startswith("menu_"):
            await dispatch_menu(data, query.message.reply_text, uid=uid)
        elif data.startswith("execute:"):
            token = data.removeprefix("execute:")
            plan = load_pending() or {}
            plan_id = plan.get("plan_id")
            if not isinstance(plan_id, str) or not hmac.compare_digest(token, short_id(plan_id)):
                await query.message.reply_text(
                    "❌ This confirmation is stale or does not match the current plan. Run /approve again.",
                    reply_markup=main_kb(),
                )
            else:
                await query.message.reply_text(approve_plan(uid, plan_id), reply_markup=main_kb())
        elif data.startswith("keep_") or data.startswith("exclude_"):
            if not authorized(uid):
                await query.message.reply_text("❌ Not authorized to edit keep/exclude prefs.", reply_markup=main_kb())
                return
            action, pid_str = data.split("_", 1)
            try:
                pid = int(pid_str)
            except ValueError:
                await query.message.reply_text("Invalid player id.", reply_markup=main_kb())
            else:
                player_name = f"player {pid}"
                try:
                    bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
                    element = next((row for row in bootstrap.get("elements", []) if int(row.get("id", -1)) == pid), None)
                    if element:
                        player_name = str(element.get("web_name") or player_name)
                except Exception:
                    pass
                prefs = load_player_prefs()
                other = "exclude" if action == "keep" else "keep"
                cur = set(prefs.get(action, []))
                oth = set(prefs.get(other, []))
                if pid in cur:
                    cur.discard(pid)
                    msg = f"Removed {player_name} from {'Keep' if action == 'keep' else 'Exclude'}."
                else:
                    cur.add(pid)
                    oth.discard(pid)
                    prefs[other] = sorted(oth)
                    verb = "KEEP" if action == "keep" else "EXCLUDE"
                    msg = f"✅ {player_name} marked {verb} • applies on the next simulation."
                prefs[action] = sorted(cur)
                save_player_prefs(prefs)
                kb = await prefs_kb(action)
                await query.message.reply_text(msg + "\nTap another player, or ← Back:",
                                               reply_markup=kb or main_kb())
        elif data == "chip_clear":
            if not authorized(uid):
                await query.message.reply_text("❌ Not authorized to change chips.", reply_markup=main_kb())
            else:
                plan = load_pending() or {}
                staged = plan.pop("chip", None)
                plan.pop("chip_gw", None)
                if plan:
                    save_pending(plan)
                message = f"🧹 Cleared staged chip <b>{html.escape(str(staged))}</b>." if staged else "ℹ️ No chip was staged."
                await query.message.reply_text(message, parse_mode="HTML", reply_markup=chip_kb())
        elif data.startswith("chip_"):
            key = data.replace("chip_", "")
            await query.message.reply_text(chip_text([key], uid), reply_markup=main_kb())
        else:
            await query.message.reply_text(
                "⚠️ This button is old or no longer valid. Open the current menu and try again.",
                reply_markup=main_kb(),
            )

    print(f"🤖 {me} polling started. Allowed chats: {allowed}")
    # resilience: network blips (TM Net) raise Timeout errors that can kill
    # run_polling - wrap it so the process survives and resumes polling.
    # CRITICAL: python-telegram-bot closes its event loop when run_polling
    # exits (even on error). Reusing the same Application object then fails
    # forever with 'RuntimeError: Event loop is closed'. The Application MUST
    # be rebuilt on every attempt so each retry gets a fresh event loop.
    while True:
        try:
            app = Application.builder().token(token).build()
            app.add_handler(CommandHandler("start", cmd_start))
            app.add_handler(CommandHandler("help", cmd_help))
            app.add_handler(CommandHandler("requestleague", cmd_requestleague))
            app.add_handler(CommandHandler("status", cmd_status))
            app.add_handler(CommandHandler("team", cmd_team))
            app.add_handler(CommandHandler("simulate", cmd_simulate))
            app.add_handler(CommandHandler("approve", cmd_approve))
            app.add_handler(CommandHandler("reject", cmd_reject))
            app.add_handler(CommandHandler("chip", cmd_chip))
            app.add_handler(CommandHandler("history", cmd_history))
            app.add_handler(CommandHandler("league", cmd_league))
            app.add_handler(CommandHandler("whoami", cmd_whoami))
            app.add_handler(CommandHandler("haaland", cmd_haaland))
            # docked reply-keyboard taps arrive as text -> route menu labels
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
            app.add_handler(CallbackQueryHandler(on_callback))
            app.run_polling(allowed_updates=["message", "callback_query"])
            # A normal return means a stop signal was handled. Exit main so
            # systemd can complete shutdown instead of retrying on a loop that
            # python-telegram-bot has already closed.
            return
        except Exception as e:
            print(f"⚠️ polling error ({repr(e)[:120]}) - restarting in 8s")
            time.sleep(8)


if __name__ == "__main__":
    main()
