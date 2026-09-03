"""
FPL Autopilot - Telegram bot service (interactive control panel).

Commands:
  /start  - welcome + mode explanation
  /status - squad value, bank, free transfers, next deadline countdown
  /team   - starting XI + bench with this-GW xPts
  /live   - in-gameweek: live points, yet-to-play, autosubs, bonus
  /lineup - best XI + captain; one tap to set it (lineup only, reversible)
  /simulate - run the pre-deadline pipeline now and show the plan
  /approve  - execute the pending plan (transfers + lineup + captain)
  /reject   - discard the pending plan, keep squad as-is
  /chip <name> - stage a chip (wildcard|freehit|benchboost|triplecaptain)
  /history - last 6 GWs: points, rank, rank change
  /compare A vs B - head-to-head player card (read-only)
  /plan - the 3-gameweek forward transfer plan (read-only)

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
    ("⚔️ Catch Up", "war_catch"),
    ("🧢 Captain Pick", "war_captpick"),
    ("📅 Fixtures", "war_fixtures"),
    ("🕵️ Rivals", "war_rivals"),
    ("👑 Rival Caps", "war_captain"),
    ("💷 Market", "war_market"),
    ("🔄 Refresh", "war_refresh"),
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


_VALID_FORMATIONS = [
    (d, m, f)
    for d in range(3, 6)
    for m in range(2, 6)
    for f in range(1, 4)
    if d + m + f == 10
]
_LINEUP_POS_RANK = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}


def _sh(name, k=13):
    name = str(name)
    return name if len(name) <= k else name[: k - 1] + "…"


def _best_xi(squad):
    """Pick the best 11 from a 15-man squad.

    Each entry is a dict with ``pos`` in GKP/DEF/MID/FWD and a numeric ``xp``.
    Returns (starters, bench, formation). Bench = reserve GK first, then the
    outfield reserves by xPts (FPL autosub order). Pure; no FPL writes.
    """
    by_pos = {
        pos: sorted((s for s in squad if s["pos"] == pos), key=lambda s: -s["xp"])
        for pos in ("GKP", "DEF", "MID", "FWD")
    }
    if not by_pos["GKP"]:
        return sorted(squad, key=lambda s: -s["xp"])[:11], [], "?"
    best = None
    for d, m, f in _VALID_FORMATIONS:
        if len(by_pos["DEF"]) < d or len(by_pos["MID"]) < m or len(by_pos["FWD"]) < f:
            continue
        chosen = (by_pos["GKP"][:1] + by_pos["DEF"][:d]
                  + by_pos["MID"][:m] + by_pos["FWD"][:f])
        total = sum(s["xp"] for s in chosen)
        if best is None or total > best[0]:
            best = (total, chosen, f"{d}-{m}-{f}")
    if best is None:
        return sorted(squad, key=lambda s: -s["xp"])[:11], [], "?"
    _, starters, formation = best
    start_ids = {s["id"] for s in starters}
    reserves = [s for s in squad if s["id"] not in start_ids]
    bench = ([s for s in reserves if s["pos"] == "GKP"]
             + sorted((s for s in reserves if s["pos"] != "GKP"), key=lambda s: -s["xp"]))
    starters.sort(key=lambda s: (_LINEUP_POS_RANK[s["pos"]], -s["xp"]))
    return starters, bench, formation


def lineup_text():
    """Advisory best XI + captain from the current 15. Never writes to FPL."""
    import glob as _glob

    client = FPLClient()
    settings = load_settings()
    bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    fixtures = fetch("https://fantasy.premierleague.com/api/fixtures/")
    els = {e["id"]: e for e in bootstrap["elements"]}
    gw = next((ev["id"] for ev in bootstrap["events"] if not ev["finished"]), 1)
    gw_so_far = max(0, gw - 1)
    fdr = {}
    for fx in fixtures:
        if fx.get("event") == gw:
            fdr[fx["team_h"]] = fx["team_h_difficulty"]
            fdr[fx["team_a"]] = fx["team_a_difficulty"]

    proj = {}
    try:
        preds = sorted(_glob.glob(os.path.join(BASE, "data", "processed", "predictions_gw*.json")))
        if preds:
            with open(preds[-1], encoding="utf-8") as fh:
                proj = {int(p["id"]): float(p.get("xpts") or 0)
                        for p in (json.load(fh).get("players") or [])
                        if p.get("id") is not None}
    except Exception:
        proj = {}

    team = client.my_team(settings["team_id"])
    picks = team.get("picks", [])
    if len(picks) < 15:
        return "🧩 <b>LINEUP</b>\nCould not read a full 15-man squad — try again shortly."

    squad = []
    for p in picks:
        e = els.get(p["element"])
        if not e:
            continue
        f = fdr.get(e["team"], 3)
        if p["element"] in proj:
            xp = proj[p["element"]]
        elif gw_so_far == 0:
            xp = preseason_xpts(e, f)
        else:
            xp = inseason_xpts_from_bootstrap(e, f, gw_so_far)
        squad.append({
            "id": p["element"], "name": e["web_name"],
            "pos": POS_MAP[e["element_type"]], "xp": float(xp),
            "was_starter": p.get("multiplier", 0) > 0,
            "was_captain": bool(p.get("is_captain")),
        })

    starters, bench, formation = _best_xi(squad)
    if not starters:
        return "🧩 <b>LINEUP</b>\nNot enough data to build an XI right now."
    cap = max(starters, key=lambda s: s["xp"])
    vice = max((s for s in starters if s["id"] != cap["id"]), key=lambda s: s["xp"], default=cap)

    start_ids = {s["id"] for s in starters}
    cur_start_ids = {s["id"] for s in squad if s["was_starter"]}
    cur_cap = next((s["id"] for s in squad if s["was_captain"]), None)
    optimal = start_ids == cur_start_ids and cur_cap == cap["id"]

    rows = [("POS", "PLAYER", "xPts", "")]
    for s in starters:
        mark = "▲" if s["id"] not in cur_start_ids else ""
        tag = "C" if s["id"] == cap["id"] else ("V" if s["id"] == vice["id"] else "")
        rows.append((s["pos"], _sh(s["name"]), f"{s['xp']:.1f}", (mark + tag)))
    widths = [max(len(str(r[i])) for r in rows) for i in range(4)]
    body = "\n".join(
        f"{r[0]:<{widths[0]}}  {r[1]:<{widths[1]}}  {r[2]:>{widths[2]}}  {r[3]}".rstrip()
        for r in rows
    )

    cur_xp = sum(s["xp"] for s in squad if s["id"] in cur_start_ids)
    new_xp = sum(s["xp"] for s in starters)

    lines = [f"🧩 <b>LINEUP — GW{gw}</b> · {formation}"]
    if optimal:
        lines.append("✅ Your XI and captain already match the projection.")
    lines.append(f"<pre>{html.escape(body)}</pre>")
    lines.append(
        f"<b>Captain:</b> {html.escape(cap['name'])} ({cap['xp']:.1f})  ·  "
        f"<b>Vice:</b> {html.escape(vice['name'])}"
    )
    lines.append("<b>Bench:</b> " + " · ".join(
        f"{i}.{html.escape(_sh(b['name'], 12))}" for i, b in enumerate(bench, 1)))
    if not optimal:
        lines.append(f"Δ XI projection: <b>{new_xp - cur_xp:+.1f}</b> xPts")

    plan = {
        "team_id": settings["team_id"], "gw": gw,
        "picks": _lineup_picks_payload(starters, bench, cap["id"], vice["id"]),
        "captain": {"id": cap["id"], "name": cap["name"]},
        "vice": {"id": vice["id"], "name": vice["name"]},
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "optimal": bool(optimal),
    }
    plan["lineup_id"] = _lineup_hash(plan["picks"])
    try:
        from atomic_io import atomic_write_json
        atomic_write_json(PENDING_LINEUP_FILE, plan)
    except Exception:
        pass

    if optimal:
        lines.append("\n<i>Nothing to apply — this is already your XI.</i>")
    elif execution_enabled():
        lines.append("\n<i>Tap ✅ Set this lineup to send this XI + captain to FPL "
                     "(lineup only, reversible). Not while a chip plan is staged — use /approve for that.</i>")
    else:
        lines.append("\n<i>Advisory only — set it in the FPL app. (Execution is off on this bot.)</i>")
    return _safe_card(lines)


PENDING_LINEUP_FILE = os.path.join(BASE, "data", "processed", "pending_lineup.json")
_LINEUP_SLOT_ORDER = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}


def _lineup_picks_payload(starters, bench, cap_id, vice_id):
    """Build the /api/my-team picks list from a solved XI + bench."""
    ordered_starters = sorted(starters, key=lambda s: (_LINEUP_SLOT_ORDER[s["pos"]], -s["xp"]))
    ordered_bench = ([b for b in bench if b["pos"] == "GKP"]
                     + [b for b in bench if b["pos"] != "GKP"])
    picks, slot = [], 1
    for s in ordered_starters:
        picks.append({"element": int(s["id"]), "position": slot,
                      "multiplier": 2 if s["id"] == cap_id else 1,
                      "is_captain": s["id"] == cap_id,
                      "is_vice_captain": s["id"] == vice_id})
        slot += 1
    for b in ordered_bench:
        picks.append({"element": int(b["id"]), "position": slot, "multiplier": 0,
                      "is_captain": False, "is_vice_captain": False})
        slot += 1
    return picks


def _lineup_hash(picks):
    import hashlib
    body = json.dumps(
        sorted((p["element"], p["position"], p["multiplier"],
                bool(p["is_captain"]), bool(p["is_vice_captain"])) for p in picks),
        separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def load_pending_lineup():
    try:
        with open(PENDING_LINEUP_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def lineup_apply_confirmation(uid=None):
    """(token, '') when the pending lineup may be applied, else (None, reason)."""
    if not authorized(uid):
        return None, "not authorized"
    if not execution_enabled():
        return None, "execution disabled"
    lp = load_pending_lineup()
    if not lp or lp.get("optimal"):
        return None, "nothing to apply"
    lineup_id = lp.get("lineup_id")
    if not isinstance(lineup_id, str):
        return None, "no lineup id"
    try:
        age = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.datetime.fromisoformat(lp["generated_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return None, "bad timestamp"
    if age > 1200:
        return None, "stale (re-run /lineup)"
    pending = load_pending() or {}
    if pending.get("status") == "pending" and pending.get("chip"):
        return None, "chip plan staged — use /approve"
    return short_id(lineup_id), ""


def apply_lineup(uid=None, token=None):
    """Apply the hash-bound pending lineup to FPL. Lineup only, no transfers."""
    if not authorized(uid):
        return "❌ Not authorized."
    if not execution_enabled():
        return "❌ Execution is disabled on this bot. No FPL write was sent."
    lp = load_pending_lineup()
    lineup_id = lp.get("lineup_id") if lp else None
    if (not isinstance(token, str) or not isinstance(lineup_id, str)
            or not hmac.compare_digest(token, short_id(lineup_id))):
        return "❌ Confirmation is stale or does not match. Run /lineup again."
    if lp.get("optimal"):
        return "✅ Your XI already matches — nothing to send."
    pending = load_pending() or {}
    if pending.get("status") == "pending" and pending.get("chip"):
        return "❌ A wildcard/free-hit plan is staged. Use /approve for that instead."
    _, deadline, _ = next_deadline_info()
    if deadline:
        try:
            left = (datetime.datetime.fromisoformat(deadline.replace("Z", "+00:00"))
                    - datetime.datetime.now(datetime.timezone.utc))
            if left < datetime.timedelta(minutes=15):
                return "❌ Too close to the deadline to set the lineup from here — use the FPL app."
        except (ValueError, TypeError):
            pass
    team_id, picks = lp["team_id"], lp["picks"]
    if os.environ.get(DRY_RUN_ENV, "1") == "1":
        return (f"🧪 DRY RUN — would set this XI, captain "
                f"{html.escape(str(lp.get('captain', {}).get('name', '?')))}. No FPL write sent.")
    client = FPLClient()
    try:
        response = client.set_lineup(team_id, picks, chip=None)
    except Exception as error:  # noqa: BLE001
        return f"❌ Lineup POST failed: {repr(error)[:140]}"
    if response.status_code >= 300:
        return f"❌ FPL rejected the lineup ({response.status_code}): {response.text[:200]}"
    matched = None
    try:
        live = client.my_team(team_id).get("picks", [])
        now_start = {p["element"] for p in live
                     if p.get("multiplier", 0) > 0 or p.get("position", 99) <= 11}
        matched = now_start == {p["element"] for p in picks if p["multiplier"] > 0}
    except Exception:  # noqa: BLE001
        matched = None
    cap_name = html.escape(str(lp.get("captain", {}).get("name", "?")))
    if matched:
        return f"✅ Lineup applied — XI set, captain {cap_name}."
    if matched is None:
        return f"⚠️ Lineup sent (couldn't verify) — check the FPL app. Captain {cap_name}."
    return f"⚠️ Lineup sent but FPL hasn't reflected it yet — check the app in a minute. Captain {cap_name}."


def _resolve_player(name, elements):
    """Best bootstrap element for a free-text name, or None."""
    q = " ".join(name.lower().split())
    if not q:
        return None
    scored = []
    for e in elements:
        web = str(e.get("web_name") or "").lower()
        full = f"{e.get('first_name', '')} {e.get('second_name', '')}".lower().strip()
        if q == web or q == full:
            rank = 0
        elif web.startswith(q) or full.startswith(q):
            rank = 1
        elif q in web or q in full:
            rank = 2
        else:
            continue
        scored.append((rank, -int(e.get("total_points") or 0), e))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]))
    return scored[0][2]


def compare_text(query):
    """Head-to-head card for two players. Read-only."""
    parts = [p.strip() for p in (query or "").lower()
             .replace(" vs ", "|").replace(" v ", "|").replace(",", "|")
             .replace(" / ", "|").split("|") if p.strip()]
    if len(parts) != 2:
        return ("⚖️ <b>COMPARE</b>\nUsage: <code>/compare salah vs palmer</code> "
                "(or <code>/compare haaland, isak</code>)")
    bs = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    fx = fetch("https://fantasy.premierleague.com/api/fixtures/")
    els = bs["elements"]
    tshort = {t["id"]: t["short_name"] for t in bs["teams"]}
    pos_map = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    gw = next((ev["id"] for ev in bs["events"] if not ev["finished"]), 1)

    a, b = _resolve_player(parts[0], els), _resolve_player(parts[1], els)
    missing = [parts[i] for i, p in enumerate((a, b)) if p is None]
    if missing:
        return f"⚖️ <b>COMPARE</b>\nCouldn't find: {html.escape(', '.join(missing))}"

    nf = {}
    for f in fx:
        ev = f.get("event")
        if ev and gw <= ev < gw + 5:
            nf.setdefault(f["team_h"], []).append((ev, tshort[f["team_a"]], f["team_h_difficulty"]))
            nf.setdefault(f["team_a"], []).append((ev, tshort[f["team_h"]], f["team_a_difficulty"]))

    def fdr_sum(tid):
        return sum(d for _, _, d in sorted(nf.get(tid, []))[:5])

    def fixstr(tid):
        return " ".join(f"{o}{d}" for _, o, d in sorted(nf.get(tid, []))[:5]) or "-"

    def sp(e):
        tags = []
        if e.get("penalties_order") == 1:
            tags.append("pens")
        if e.get("corners_and_indirect_freekicks_order") == 1:
            tags.append("corners")
        if e.get("direct_freekicks_order") == 1:
            tags.append("FK")
        return ", ".join(tags) or "-"

    def status(e):
        cop = e.get("chance_of_playing_next_round")
        if e.get("status") != "a":
            return f"{e.get('status')} {cop}%" if cop is not None else str(e.get("status"))
        return f"{cop}%" if cop is not None else "fit"

    rows = [
        ("", _sh(a["web_name"], 12), _sh(b["web_name"], 12)),
        ("Team", tshort[a["team"]], tshort[b["team"]]),
        ("Pos", pos_map[a["element_type"]], pos_map[b["element_type"]]),
        ("Price", f"£{a['now_cost'] / 10:.1f}", f"£{b['now_cost'] / 10:.1f}"),
        ("Owned", f"{a['selected_by_percent']}%", f"{b['selected_by_percent']}%"),
        ("Form", str(a["form"]), str(b["form"])),
        ("PPG", str(a["points_per_game"]), str(b["points_per_game"])),
        ("Total", str(a["total_points"]), str(b["total_points"])),
        ("xGI/90", str(a.get("expected_goal_involvements_per_90", "-")),
         str(b.get("expected_goal_involvements_per_90", "-"))),
        ("Set-pc", sp(a), sp(b)),
        ("Status", status(a), status(b)),
        ("Next5 FDR", str(fdr_sum(a["team"])), str(fdr_sum(b["team"]))),
    ]
    w = [max(len(str(r[i])) for r in rows) for i in range(3)]
    body = "\n".join(
        f"{r[0]:<{w[0]}}  {str(r[1]):<{w[1]}}  {r[2]}" for r in rows)

    lines = [
        f"⚖️ <b>COMPARE — GW{gw}</b>",
        f"<pre>{html.escape(body)}</pre>",
        f"<b>{html.escape(a['web_name'])}</b> next 5: <code>{html.escape(fixstr(a['team']))}</code>",
        f"<b>{html.escape(b['web_name'])}</b> next 5: <code>{html.escape(fixstr(b['team']))}</code>",
        "\n<i>Lower FDR = easier. Read-only.</i>",
    ]
    return _safe_card(lines)


def plan_horizon_text():
    """The 3-GW forward plan from the horizon MILP already on the pending plan."""
    plan = load_pending() or {}
    hp = plan.get("horizon_plan") or {}
    weeks = hp.get("weeks") or []
    base_gw = int(plan.get("gw") or next_gw_id())
    if not weeks:
        chip = str(plan.get("chip") or "")
        why = (f" (this plan is a {chip} rebuild — no week-by-week sequence)"
               if chip else " — run /simulate on a normal week")
        return f"🗺️ <b>3-GW PLAN</b>\nNo multi-week plan available{why}."

    els = {}
    try:
        els = {e["id"]: e["web_name"] for e in
               fetch("https://fantasy.premierleague.com/api/bootstrap-static/")["elements"]}
    except Exception:
        els = {}

    lines = [f"🗺️ <b>3-GW PLAN</b> · from GW{base_gw}"
             f"  <i>(proj {hp.get('objective', '?')})</i>"]
    for wk in weeks[:3]:
        gw = base_gw + int(wk.get("gw_offset", 0))
        moves = wk.get("transfers") or []
        hits = int(wk.get("hits") or 0)
        pts = wk.get("robust_points_with_captain", wk.get("mean_points_with_captain"))
        cap = els.get(wk.get("captain_id"), str(wk.get("captain_id", "?")))
        head = (f"\n<b>GW{gw}</b> · {wk.get('formation', '?')} · "
                f"(C) {html.escape(str(cap))} · ~{pts} pts"
                + (f" · <b>−{4 * hits}</b> hit" if hits else ""))
        lines.append(head)
        if moves:
            for m in moves:
                lines.append(f"  {html.escape(_sh(m.get('out_name'), 13))} → "
                             f"{html.escape(_sh(m.get('in_name'), 13))}")
        else:
            lines.append("  hold — no transfer")
        lines.append(f"  FT {wk.get('free_transfers_before')}→{wk.get('free_transfers_after')} · "
                     f"bank £{(wk.get('bank_after') or 0) / 10:.1f}m")
    lines.append("\n<i>Forward guidance only — only GW" + str(base_gw)
                 + " is staged for approval. Later weeks re-plan each /simulate.</i>")
    return _safe_card(lines)


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
        chip = str(plan.get("chip") or "").lower()
        # A Wildcard / Free Hit rebuild does not consume free transfers and the
        # executor plays the chip itself (chip= on the transfers POST), so the
        # FT count is not a staleness signal for a chip plan. FPL also only sets
        # transfers.status = "unlimited" once the wildcard editing session is
        # entered in the UI, which is not required to execute.
        if (chip not in ("wildcard", "freehit")
                and live_transfers.get("status") != "unlimited"
                and plan.get("free_transfers_before") is not None):
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


def live_text():
    """In-gameweek tracker: live points, players yet to play, autosubs,
    captain status, provisional bonus. Read-only."""
    client = FPLClient()
    settings = load_settings()
    team_id = settings["team_id"]
    bs = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    els = {e["id"]: e for e in bs["elements"]}
    cur = next((ev for ev in bs["events"] if ev.get("is_current")), None)
    nxt = next((ev for ev in bs["events"] if ev.get("is_next")), None)
    if not cur:
        when = f" — next GW{nxt['id']} {nxt['deadline_time']}" if nxt else ""
        return f"⚡ <b>LIVE</b>\nNo gameweek is live right now{when}."
    gw = cur["id"]
    try:
        live = fetch(f"https://fantasy.premierleague.com/api/event/{gw}/live/")
        picks_payload = client.get_json(f"entry/{team_id}/event/{gw}/picks/")
        fixtures = fetch(f"https://fantasy.premierleague.com/api/fixtures/?event={gw}")
    except Exception as exc:
        return f"⚡ <b>LIVE — GW{gw}</b>\nLive feed unavailable ({repr(exc)[:80]})."

    lstats = {e["id"]: e for e in live.get("elements", [])}
    picks = picks_payload.get("picks") or []
    eh = picks_payload.get("entry_history") or {}
    active_chip = picks_payload.get("active_chip")

    fx_by_team = {}
    for fx in fixtures:
        started = bool(fx.get("started"))
        finished = bool(fx.get("finished") or fx.get("finished_provisional"))
        for side in ("team_h", "team_a"):
            fx_by_team.setdefault(fx.get(side), []).append((started, finished))

    def played(eid):
        return int((lstats.get(eid, {}).get("stats") or {}).get("minutes") or 0)

    def team_state(eid):
        rows = fx_by_team.get(els.get(eid, {}).get("team"), [])
        if not rows:
            return "none"
        if all(f for _, f in rows):
            return "done"
        if any(s for s, _ in rows):
            return "live"
        return "upcoming"

    xi = [p for p in picks if p.get("position", 99) <= 11]
    bench = [p for p in picks if p.get("position", 99) > 11]
    bench_boost = active_chip == "bboost"

    live_pts = 0
    yet_to_play, autosubs = [], []
    for p in xi:
        eid = p["element"]
        pts = int((lstats.get(eid, {}).get("stats") or {}).get("total_points") or 0)
        mult = p.get("multiplier", 1)
        if played(eid) == 0 and team_state(eid) == "done" and not bench_boost:
            # autosub: first eligible bench player who has appeared
            starter_pos = els.get(eid, {}).get("element_type")
            for b in bench:
                bp = els.get(b["element"], {}).get("element_type")
                same_gk = (starter_pos == 1) == (bp == 1)
                if same_gk and played(b["element"]) > 0 and b["element"] not in {a[1] for a in autosubs}:
                    autosubs.append((eid, b["element"]))
                    pts = int((lstats.get(b["element"], {}).get("stats") or {}).get("total_points") or 0)
                    break
        live_pts += pts * mult
        if team_state(eid) in ("upcoming", "live") and played(eid) == 0:
            yet_to_play.append(els.get(eid, {}).get("web_name", str(eid)))

    bench_pts = sum(int((lstats.get(b["element"], {}).get("stats") or {}).get("total_points") or 0)
                    for b in bench)
    cap = next((p for p in xi if p.get("is_captain")), None)
    cap_line = ""
    if cap:
        ce = els.get(cap["element"], {})
        cp = int((lstats.get(cap["element"], {}).get("stats") or {}).get("total_points") or 0)
        cap_line = (f"\n👑 (C) {html.escape(ce.get('web_name', '?'))}: "
                    f"{cp} → <b>{cp * cap.get('multiplier', 2)}</b> · {team_state(cap['element'])}")

    avg = cur.get("average_entry_score")
    lines = [f"⚡ <b>LIVE — GW{gw}</b>" + (f" · {active_chip}" if active_chip else "")]
    tail = f"  ·  avg {avg} ({live_pts - avg:+d})" if isinstance(avg, int) else ""
    lines.append(f"You: <b>{live_pts}</b> pts{tail}{cap_line}")
    if eh.get("overall_rank"):
        lines.append(f"Overall (live): ~{int(eh['overall_rank']):,}")
    lines.append(f"Bench: {bench_pts} pts" + (" (boosted, counting)" if bench_boost else " idle"))
    if autosubs:
        lines.append("Autosubs: " + " · ".join(
            f"{html.escape(els.get(i, {}).get('web_name', '?'))} ↑ for "
            f"{html.escape(els.get(o, {}).get('web_name', '?'))}" for o, i in autosubs))
    if yet_to_play:
        lines.append(f"Yet to play ({len(yet_to_play)}): "
                     + ", ".join(html.escape(n) for n in yet_to_play[:11]))
    else:
        lines.append("All your players have played.")

    bps_rows = sorted(
        ((els.get(p["element"], {}).get("web_name", "?"),
          int((lstats.get(p["element"], {}).get("stats") or {}).get("bps") or 0))
         for p in xi), key=lambda r: -r[1])[:3]
    if bps_rows and bps_rows[0][1] > 0:
        lines.append("\nYour BPS: " + " · ".join(f"{html.escape(n)} {v}" for n, v in bps_rows)
                     + "  <i>(bonus provisional)</i>")
    lines.append("\n<i>Live from the official feed. Refresh with /live.</i>")
    return _safe_card(lines)


def league_text(state=None):
    """Lean league war-room overview: where you stand, the sharp money, and the
    closest rivals. Everything else lives behind the Rivals / Captains / Market
    buttons. Never mutates the FPL team.
    """
    import json as _json
    import os as _os
    latest = _os.path.join(BASE, "data", "processed", "league_intelligence", "latest.json")
    if state is None:
        if not _os.path.exists(latest):
            return ("🏆 <b>WAR ROOM</b>\n"
                    "No intelligence snapshot yet — tap <b>Refresh</b>. It only reads "
                    "public FPL endpoints and cannot change your team.")
        try:
            state = _json.load(open(latest, encoding="utf-8"))
        except (OSError, ValueError):
            return "🏆 <b>WAR ROOM</b>\nSnapshot unreadable — tap <b>Refresh</b>."

    event = state.get("event", "?")
    mode = (state.get("mode") or {}).get("mode", "Neutral")
    lines = [f"🏆 <b>WAR ROOM · GW{html.escape(str(event))} · {html.escape(str(mode))}</b>"]

    # --- where you stand, per league ---
    for row in state.get("prize_status", []) or []:
        lid = html.escape(str(row.get("league_id")))
        if row.get("rank") is None:
            lines.append(f"\n<b>L{lid}</b> · standings not live yet")
            continue
        current = html.escape(str((row.get("current_prize") or {}).get("prize") or "outside prize"))
        lines.append(f"\n<b>L{lid}</b> · rank {int(row['rank'])} · {current}")
        nxt = row.get("next_target") or {}
        prob = row.get("probability") or {}
        if nxt:
            bit = f"  → {html.escape(str(nxt.get('prize')))}"
            if row.get("gap_to_next_target") is not None:
                bit += f" {_safe_number(row.get('gap_to_next_target')):.0f} pts"
            if prob.get("available"):
                bit += f" · P(top10) {_safe_number(prob.get('p_top_10')):.1f}%"
            lines.append(bit)

    # --- monthly, only when there is real data ---
    for row in (state.get("monthly_status") or [])[:2]:
        if row.get("rank") is not None:
            prize = html.escape(str((row.get("prize") or {}).get("prize") or "monthly"))
            lines.append(f"📅 L{row.get('league_id')} monthly · rank {int(row['rank'])} · "
                         f"{_safe_number(row.get('gap_to_first')):.0f} to 1st · {prize}")

    # --- live standing ---
    swing = state.get("live_swing") or {}
    if swing:
        rivals = swing.get("rivals") or []
        text = f"\n⚡ <b>Live:</b> us {_safe_number(swing.get('our_live_points')):.0f} pts"
        if rivals:
            top = rivals[0]
            text += f" · leader {_safe_number(top.get('live_points')):.0f} ({_safe_number(top.get('swing_vs_us')):+.0f})"
        lines.append(text)

    # --- sharp money (rival transfer consensus) ---
    moves = state.get("transfer_consensus") or []
    if moves:
        lines.append("\n🔄 <b>Sharp money</b>")
        for m in moves[:5]:
            lines.append(
                f"  {html.escape(str(m.get('name') or m.get('element')))}  "
                f"+{_safe_number(m.get('weighted_in_pct')):.0f}% / −{_safe_number(m.get('weighted_out_pct')):.0f}%"
            )

    # --- closest sharp rivals ---
    cohort = sorted(
        state.get("cohort") or [],
        key=lambda r: -_safe_number(r.get("live_sharpness", r.get("historical_score", 50)), 50),
    )[:5]
    if cohort:
        lines.append("\n🚨 <b>Sharp rivals</b>")
        for r in cohort:
            act = (r.get("activity") or {}).get("archetype_live") or r.get("archetype") or ""
            score = _safe_number(r.get("live_sharpness", r.get("historical_score", 50)), 50)
            tail = f" · {html.escape(str(act))}" if act else f" · tier {html.escape(str(r.get('tier') or '?'))}"
            lines.append(f"  {html.escape(str(r.get('team_name') or 'Unknown'))} · sharp {score:.0f}{tail}")

    # --- footer ---
    market = state.get("market_signals") or []
    actionable = sum(1 for row in market
                     if _market_direction(row) in {"rise", "fall"} or row.get("chance_next") is not None)
    reg = (state.get("registry") or {}).get("status")
    foot = f"\n<i>{int(state.get('cohort_count', 0))} rivals tracked · {actionable} market signals"
    if str(reg).lower() == "final":
        foot += " · registry FINAL"
    foot += "</i>"
    lines.append(foot)

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
    """Render a compact advisory card; never mutate the user's FPL team.

    Sections: overview (the lean league_text card), rivals, captain, market.
    Prize race / transfer radar / registry / attack-plan were folded into the
    overview or dropped in the war-room revamp.
    """
    state, missing = _league_state_or_message(state)
    if missing:
        return missing
    event = state.get("event", "?")
    mode = state.get("mode") or {}

    if section == "overview":
        return league_text(state)

    if section == "rivals":
        cohort = state.get("cohort") or []
        standings = state.get("standings") or []
        our_entry = state.get("our_entry")
        total_by_entry, rank_by_key = {}, {}
        for r in standings:
            total_by_entry.setdefault(r.get("entry"), r.get("total"))
            rank_by_key[(r.get("entry"), r.get("league_id"))] = r.get("rank")
        our_total = _safe_number(total_by_entry.get(our_entry))

        wc = sum(1 for r in cohort if "wildcard" in ((r.get("activity") or {}).get("chips_unseen") or []))
        fh = sum(1 for r in cohort if "freehit" in ((r.get("activity") or {}).get("chips_unseen") or []))
        lines = [f"🕵️ <b>SHARP RIVALS — GW{event}</b>",
                 f"{len(cohort)} tracked · {wc} still hold Wildcard · {fh} hold Free Hit"]

        # the ones actually in your prize places
        threats = []
        for row in cohort:
            leagues = [int(x.split("_league_")[-1]) for x in (row.get("reasons") or [])
                       if x.startswith("top_") and "_league_" in x]
            if not leagues:
                continue
            best_rank = min((rank_by_key.get((row.get("entry"), lg)) or 9999) for lg in leagues)
            tot = _safe_number(total_by_entry.get(row.get("entry")))
            threats.append((tot, best_rank, row.get("team_name") or "?", leagues))
        threats.sort(key=lambda t: -t[0])

        if threats:
            lines.append(f"\n🎯 <b>In your prize places</b> ({len(threats)})")
            rows = [("#", "TEAM", "TOTAL", "vs you")]
            for tot, rank, name, _lg in threats[:8]:
                rows.append((str(rank), str(name)[:15], f"{tot:.0f}", f"{tot - our_total:+.0f}"))
            w = [max(len(str(c)) for c in col) for col in zip(*rows)]
            body = "\n".join("  ".join(str(c).ljust(w[i]) if i == 1 else str(c).rjust(w[i])
                                       for i, c in enumerate(r)) for r in rows)
            lines.append(f"<pre>{html.escape(body)}</pre>")

        # sharpest of the rest (evidence, not the race)
        rest = sorted(
            (r for r in cohort if not any(x.startswith("top_") for x in (r.get("reasons") or []))),
            key=lambda r: -_safe_number(r.get("live_sharpness", r.get("historical_score", 50)), 50),
        )[:4]
        if rest:
            lines.append("\n<b>Also sharp</b> (evidence, not chasing you): "
                         + ", ".join(html.escape(str(r.get("team_name") or "?")) for r in rest))
        if not cohort:
            lines.append("No rival cohort available yet.")
        return _safe_card(lines)

    if section == "captain":
        exposure = sorted(
            (state.get("player_exposure") or {}).values(),
            key=lambda row: (-_safe_number(row.get("captain_share")), -_safe_number(row.get("effective_ownership"))),
        )
        pending = load_pending() or {}
        our_cap = pending.get("captain") or {}
        our_name = str(our_cap.get("name") or our_cap.get("id") or "")
        locked = state.get("exposure_event")
        lines = [f"👑 <b>RIVAL CAPTAINS — GW{event}</b>"]
        if locked and locked != event:
            lines.append(f"<i>from rivals' locked GW{locked} squads</i>")
        if not exposure:
            lines.append(f"No trusted locked-squad sample yet "
                         f"({state.get('trusted_pick_count', 0)}/{state.get('cohort_count', 0)} squads).")
            return _safe_card(lines)

        rows = [("", "PLAYER", "C%", "EO%")]
        our_row = None
        for row in exposure[:8]:
            name = str(row.get("name") or row.get("element"))
            mark = "►" if our_name and name.lower() == our_name.lower() else " "
            r = (mark, name[:14], f"{_safe_number(row.get('captain_share')):.0f}",
                 f"{_safe_number(row.get('effective_ownership')):.0f}")
            rows.append(r)
            if mark == "►":
                our_row = row
        widths = [max(len(str(c)) for c in col) for col in zip(*rows)]
        body = "\n".join("  ".join(str(c).ljust(widths[i]) if i < 2 else str(c).rjust(widths[i])
                                   for i, c in enumerate(r)) for r in rows)
        lines.append(f"<pre>{html.escape(body)}</pre>")

        # verdict on our captain vs the field
        if our_name and our_row is not None:
            share = _safe_number(our_row.get("captain_share"))
            eo = _safe_number(our_row.get("effective_ownership"))
            if share >= 40:
                verdict = (f"You're on the crowd captain ({share:.0f}%). Low variance vs the field — "
                           f"a blank costs little, a haul gains little.")
            elif share >= 15:
                verdict = f"Semi-differential ({share:.0f}% of rivals). Moderate swing either way."
            else:
                verdict = (f"Differential captain ({share:.0f}% of rivals). High variance — "
                           f"a haul gains ground on most rivals, a blank loses it.")
            if eo >= 130:
                verdict += f" EO {eo:.0f}%: most rivals rise/fall with him too."
            lines.append("\n" + verdict)
        elif our_name:
            lines.append(f"\n<b>{html.escape(our_name)}</b> is not a common rival captain — full differential.")
        return _safe_card(lines)

    if section == "catch":
        lines = [f"⚔️ <b>CATCH UP — GW{event}</b>"]
        standings = state.get("standings") or []
        our_entry = state.get("our_entry")
        last_gw = int(state.get("completed_gws") or max(1, int(event) - 1))
        try:
            bs = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
            els = {e["id"]: e.get("web_name", str(e["id"])) for e in bs.get("elements", [])}
        except Exception:
            els = {}
        client = FPLClient()

        def _picks(entry):
            try:
                raw = client.entry_picks(entry, last_gw).get("picks") or []
                ids = {p["element"] for p in raw}
                cap = next((els.get(p["element"], "?") for p in raw if p.get("is_captain")), "?")
                return ids, cap
            except Exception:
                return None, None

        my_ids, my_cap = _picks(our_entry)
        for lg in state.get("league_ids") or []:
            rows = sorted((r for r in standings if r.get("league_id") == lg),
                          key=lambda r: r.get("rank") or 10 ** 9)
            mine = next((r for r in rows if r.get("entry") == our_entry), None)
            if not mine:
                continue
            ahead = [r for r in rows if (r.get("rank") or 10 ** 9) < (mine.get("rank") or 0)]
            lines.append(f"\n━━ <b>L{lg}</b> ━━")
            if not ahead:
                lines.append(f"rank {mine.get('rank')} · {mine.get('total')} pts · 🥇 you lead")
                continue
            tgt = ahead[-1]
            tied = sum(1 for r in ahead if r.get("rank") == tgt.get("rank"))
            gap = _safe_number(tgt.get("total")) - _safe_number(mine.get("total"))
            gap_txt = "level (tiebreak)" if abs(gap) < 0.5 else f"{gap:+.0f} pts"
            lines.append(f"you rank {mine.get('rank')} · target "
                         f"<b>{html.escape(str(tgt.get('entry_name')))}</b> rank {tgt.get('rank')}"
                         f"{f' (+{tied - 1})' if tied > 1 else ''} · {gap_txt}")
            t_ids, t_cap = _picks(tgt.get("entry"))
            if t_ids is None or my_ids is None:
                lines.append("<i>squad compare needs post-deadline picks</i>")
                continue
            if t_cap and my_cap:
                same = " (same)" if t_cap == my_cap else ""
                lines.append(f"(C)  them <b>{html.escape(str(t_cap))}</b>  ·  you <b>{html.escape(str(my_cap))}</b>{same}")
            theirs = [els.get(i, str(i)) for i in list(t_ids - my_ids)[:6]]
            mine_only = [els.get(i, str(i)) for i in list(my_ids - t_ids)[:6]]
            if theirs or mine_only:
                pad = max((len(x) for x in theirs), default=0)
                pad = min(max(pad, 8), 15)
                head = "THEY OWN".ljust(pad + 3) + "YOU OWN"
                body = [head]
                for a, b in zip(theirs + [""] * 6, mine_only + [""] * 6):
                    if not a and not b:
                        break
                    body.append(a.ljust(pad + 3) + b)
                lines.append("<pre>" + html.escape("\n".join(body)) + "</pre>")
        lines.append(f"\n<i>vs locked GW{last_gw} squads. Your differentials are the climb — cover theirs only if it also lifts your xPts.</i>")
        return _safe_card(lines)

    if section == "captpick":
        import glob as _glob
        import json as _json
        import os as _os
        preds = sorted(_glob.glob(_os.path.join(BASE, "data", "processed", "predictions_gw*.json")))
        lines = [f"🧢 <b>CAPTAIN PICK — GW{event}</b>"]
        if not preds:
            return _safe_card(lines + ["No projection yet — run /simulate."])
        try:
            pr = _json.load(open(preds[-1], encoding="utf-8"))
        except (OSError, ValueError):
            return _safe_card(lines + ["Projection file unreadable."])
        exposure = {str(v.get("name") or "").lower(): v
                    for v in (state.get("player_exposure") or {}).values()}
        modeword = str((state.get("mode") or {}).get("mode", "Neutral"))
        cands = sorted((p for p in pr.get("players") or [] if p.get("pos") in ("MID", "FWD", "DEF")),
                       key=lambda p: -_safe_number(p.get("xpts")))[:8]
        if not cands:
            return _safe_card(lines + ["No candidates in the projection."])
        table = [("PLAYER", "xPts", "start", "rivC%")]
        for p in cands:
            exp = exposure.get(str(p.get("name") or "").lower(), {})
            table.append((str(p.get("name"))[:13], f"{_safe_number(p.get('xpts')):.1f}",
                          f"{_safe_number(p.get('p_start')) * 100:.0f}",
                          f"{_safe_number(exp.get('captain_share')):.0f}"))
        w = [max(len(str(c)) for c in col) for col in zip(*table)]
        body = "\n".join("  ".join(str(c).ljust(w[i]) if i == 0 else str(c).rjust(w[i])
                                   for i, c in enumerate(r)) for r in table)
        lines.append(f"<pre>{html.escape(body)}</pre>")

        def _cshare(p):
            return _safe_number(exposure.get(str(p.get("name") or "").lower(), {}).get("captain_share"))
        safe_pick = max(cands, key=_cshare)
        punt = max(cands, key=lambda p: _safe_number(p.get("upside")) - _cshare(p) * 0.1)
        our = str((load_pending() or {}).get("captain", {}).get("name") or "")
        lean = ("lean the Punt to gain ground" if modeword == "Chase"
                else "lean the Safe pick to protect" if modeword == "Protect"
                else "take the highest xPts")
        lines.extend([
            f"\n<b>Highest xPts:</b> {html.escape(str(cands[0].get('name')))} ({_safe_number(cands[0].get('xpts')):.1f})",
            f"<b>Safe (crowd):</b> {html.escape(str(safe_pick.get('name')))} · {_cshare(safe_pick):.0f}% of rivals",
            f"<b>Punt (ceiling):</b> {html.escape(str(punt.get('name')))} · upside {_safe_number(punt.get('upside')):.1f}",
            f"\nMode <b>{html.escape(modeword)}</b>: {lean}. Pending (C): {html.escape(our) or '—'}",
        ])
        return _safe_card(lines)

    if section == "fixtures":
        lines = [f"📅 <b>FIXTURES — GW{event}+</b>"]
        try:
            fx = fetch("https://fantasy.premierleague.com/api/fixtures/")
            bs = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
        except Exception as exc:
            return _safe_card(lines + [f"FPL fixtures unavailable ({repr(exc)[:60]})."])
        tshort = {t["id"]: t["short_name"] for t in bs.get("teams", [])}
        gws = list(range(int(event), int(event) + 6))
        cnt, fdr = {}, {}
        for f in fx:
            ev = f.get("event")
            if ev not in gws:
                continue
            for side, diff in (("team_h", "team_h_difficulty"), ("team_a", "team_a_difficulty")):
                tid = f.get(side)
                cnt[(tid, ev)] = cnt.get((tid, ev), 0) + 1
                fdr.setdefault((tid, ev), []).append(f.get(diff) or 3)
        flagged = False
        for ev in gws:
            dgw = sorted({tshort.get(t, "?") for (t, e) in cnt if e == ev and cnt[(t, e)] >= 2})
            bgw = sorted({tshort.get(t, "?") for t in tshort if cnt.get((t, ev), 0) == 0})
            if dgw:
                lines.append(f"🟢 <b>GW{ev} DGW</b>: {', '.join(dgw[:10])}")
                flagged = True
            if len(bgw) >= 3:
                lines.append(f"🔴 <b>GW{ev} BGW</b>: {', '.join(bgw[:10])}")
                flagged = True
        if not flagged:
            lines.append("No DGW/BGW in the next 6 — standard planning.")
        run = {}
        for t in tshort:
            vals = [min(fdr.get((t, ev), [3])) for ev in gws[:3]]
            run[t] = sum(vals) / max(1, len(vals))
        best = sorted(run, key=run.get)[:4]
        worst = sorted(run, key=run.get, reverse=True)[:4]
        lines.append("\n<b>Easiest next-3:</b> " + ", ".join(f"{tshort[t]} {run[t]:.1f}" for t in best))
        lines.append("<b>Toughest next-3:</b> " + ", ".join(f"{tshort[t]} {run[t]:.1f}" for t in worst))
        try:
            my = FPLClient().my_team(load_settings()["team_id"])
            elem = {e["id"]: e for e in bs.get("elements", [])}
            my_teams = {elem[p["element"]]["team"] for p in my.get("picks", []) if p.get("element") in elem}
            if my_teams:
                ez = min(my_teams, key=lambda t: run.get(t, 3))
                hd = max(my_teams, key=lambda t: run.get(t, 3))
                lines.append(f"\nYour squad: best run {tshort.get(ez)} ({run.get(ez, 3):.1f}) · "
                             f"worst {tshort.get(hd)} ({run.get(hd, 3):.1f})")
        except Exception:
            pass
        return _safe_card(lines)


    if section == "market":
        return _market_card(state, "market")

    return league_text(state)


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
        "⚡ Live": "menu_live",
        "🧩 Lineup": "menu_lineup",
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
        rows = [labels[0:3], labels[3:5], labels[5:7], labels[7:9], labels[9:11], labels[11:13]]
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
            "⚡ Live — in-gameweek points, yet-to-play, autosubs, bonus\n"
            "🧩 Lineup — best XI + captain from your current 15 (advisory)\n"
            "🧠 Simulate — run the full pipeline now\n"
            "✅ Approve — execute the pending plan\n"
            "❌ Reject — discard the pending plan\n"
            "🎩 Chip — stage a chip (wildcard/freehit/benchboost/triplecaptain)\n"
            "📜 History — last 6 GWs results\n"
            "⚖️ /compare A vs B — head-to-head player card\n"
            "🏆 League War Room — where you stand, the sharp money, and your closest rivals\n\n"
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

    async def cmd_lineup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        try:
            card = lineup_text()
            token, _ = lineup_apply_confirmation(update.effective_user.id)
            kb = main_kb()
            if token:
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Set this lineup", callback_data=f"applylineup:{token}"),
                    InlineKeyboardButton("Cancel", callback_data="menu_main"),
                ]])
            await update.message.reply_text(card, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await update.message.reply_text(_error_card("Lineup"), parse_mode="HTML", reply_markup=main_kb())

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

    async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        try:
            await update.message.reply_text(
                compare_text(" ".join(ctx.args or [])), parse_mode="HTML", reply_markup=main_kb())
        except Exception:
            await update.message.reply_text(_error_card("Compare"), parse_mode="HTML", reply_markup=main_kb())

    async def cmd_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        try:
            await update.message.reply_text(live_text(), parse_mode="HTML", reply_markup=main_kb())
        except Exception:
            await update.message.reply_text(_error_card("Live"), parse_mode="HTML", reply_markup=main_kb())

    async def cmd_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        try:
            await update.message.reply_text(plan_horizon_text(), parse_mode="HTML", reply_markup=main_kb())
        except Exception:
            await update.message.reply_text(_error_card("Plan"), parse_mode="HTML", reply_markup=main_kb())

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
        elif data == "menu_live":
            try:
                await reply(live_text(), parse_mode="HTML", reply_markup=main_kb())
            except Exception:
                await reply(_error_card("Live"), parse_mode="HTML", reply_markup=main_kb())
        elif data == "menu_lineup":
            try:
                card = lineup_text()
                token, _ = lineup_apply_confirmation(uid)
                kb = main_kb()
                if token:
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Set this lineup", callback_data=f"applylineup:{token}"),
                        InlineKeyboardButton("Cancel", callback_data="menu_main"),
                    ]])
                await reply(card, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await reply(_error_card("Lineup"), parse_mode="HTML", reply_markup=main_kb())
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
                "🏆 League War Room — where you stand, the sharp money, and your closest rivals",
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
                reply_markup=war_room_kb(),
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
        elif data.startswith("applylineup:"):
            await query.message.reply_text(
                apply_lineup(uid, data.removeprefix("applylineup:")), reply_markup=main_kb())
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
            app.add_handler(CommandHandler("lineup", cmd_lineup))
            app.add_handler(CommandHandler("simulate", cmd_simulate))
            app.add_handler(CommandHandler("approve", cmd_approve))
            app.add_handler(CommandHandler("reject", cmd_reject))
            app.add_handler(CommandHandler("chip", cmd_chip))
            app.add_handler(CommandHandler("history", cmd_history))
            app.add_handler(CommandHandler("league", cmd_league))
            app.add_handler(CommandHandler("whoami", cmd_whoami))
            app.add_handler(CommandHandler("haaland", cmd_haaland))
            app.add_handler(CommandHandler("compare", cmd_compare))
            app.add_handler(CommandHandler("live", cmd_live))
            app.add_handler(CommandHandler("plan", cmd_plan))
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
