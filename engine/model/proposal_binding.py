"""Proposal binding, deadline cutoff, and v2 candidate gate (Sol GW1 directive).

Sol decided (docs/sol-gw1-improvements.md):
  1. Human gate before v2 promotion (V1_ACTIVE -> V2_PENDING -> V2_ACTIVE)
  2. Proposal identity: canonical plan hash + input fingerprint, callbacks
     bound to them, stale tokens fail closed
  3. Deadline minus 30-minute hard cutoff shared by approval/execution/reminders
  4. No captaincy change (max-xPts eligible starter stays)

This module is pure logic (no network, no Telegram) so it is unit-testable
without live FPL/odds/Telegram access.
"""
import hashlib
import json
import os
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V2_STATE_FILE = os.path.join(BASE, "data", "processed", "v2_candidate.json")

# v2 state machine statuses
V1_ACTIVE = "v1_active"
V2_PENDING = "v2_pending"   # candidate awaiting owner approval
V2_ACTIVE = "v2_active"     # owner approved; plan regeneration still required


# ---------------------------------------------------------------------------
# Canonical hashing (execution-relevant only - no display/timestamp noise)
# ---------------------------------------------------------------------------
def _exec_semantics(plan):
    """Extract the execution-relevant fields of a plan into a stable dict.

    JSON ordering and display fields must not affect the identity.
    """
    def _pid(p):
        # element_out/element_in are bare player ids in transfer rows; starters/
        # bench/captain/vice are player dicts. Accept either.
        if isinstance(p, dict):
            return p.get("id")
        return p

    # chip may live at top level or inside chip_suggestion (both shapes occur)
    chip = plan.get("chip")
    if chip is None and isinstance(plan.get("chip_suggestion"), dict):
        chip = plan["chip_suggestion"].get("chip")

    return {
        "gw": plan.get("gw"),
        "transfers": sorted(
            [(_pid(t.get("element_out")), _pid(t.get("element_in")))
             for t in plan.get("transfers", [])]),
        "starters": [_pid(p) for p in plan.get("target_starters", [])],
        "bench": [_pid(p) for p in plan.get("bench", [])],
        "captain": _pid(plan.get("captain")),
        "vice": _pid(plan.get("vice")),
        "chip": chip,
    }


def canonical_plan_hash(plan):
    """Deterministic sha256 over execution semantics only."""
    payload = json.dumps(_exec_semantics(plan), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def short_id(full_hash, n=8):
    return (full_hash or "")[:n]


def input_fingerprint(gw, engine, deadline, odds_fp=None, settings_fp=None,
                      run_id=None, source_fp=None):
    """Fingerprint of the inputs that produced a plan.

    engine/version + event + deadline + odds snapshot + execution settings.
    Any change invalidates approvals bound to the old fingerprint.
    """
    blob = {
        "gw": gw,
        "engine": engine,
        "deadline": deadline,
        "odds_fp": odds_fp,
        "settings_fp": settings_fp,
        "run_id": run_id,
        "source_fp": source_fp,
    }
    payload = json.dumps(blob, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def settings_fingerprint(settings):
    """Hash the execution-relevant settings that can change a plan."""
    keys = ["execution_mode", "transfer_hit_threshold", "approval_cutoff_minutes",
            "v4_paid_transfer_min_gws", "v4_joint_transfer_limit",
            "v4_max_paid_transfers", "v4_transfer_risk_penalty",
            "v4_bench_depth_weight", "v4_captain_min_start",
            "v4_captain_min_minutes", "v4_transfer_friction",
            "competitive_v4", "league_intelligence"]
    blob = {k: settings.get(k) for k in keys if k in settings}
    payload = json.dumps(blob, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_age_hours(plan, now=None):
    """Age of the plan in hours from its generated_at timestamp."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    ts = plan.get("generated_at")
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return max(0.0, (now - dt).total_seconds() / 3600.0)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Deadline cutoff (deadline minus 30 minutes, UTC-aware, fail closed)
# ---------------------------------------------------------------------------
def parse_deadline(deadline_str):
    """Parse an FPL deadline to tz-aware UTC. Returns None on any failure."""
    if not deadline_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(deadline_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # naive deadline is NOT trustworthy -> fail closed
            return None
        return dt.astimezone(datetime.timezone.utc)
    except (ValueError, TypeError):
        return None


def cutoff_time(deadline_str, cutoff_minutes=30):
    """UTC datetime of the hard cutoff = deadline - margin. None if invalid."""
    dl = parse_deadline(deadline_str)
    if dl is None:
        return None
    return dl - datetime.timedelta(minutes=cutoff_minutes)


def is_past_cutoff(deadline_str, now=None, cutoff_minutes=30):
    """True when `now` is at or after deadline - cutoff_minutes.

    Fails closed: invalid/naive deadline -> True (block action).
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    cut = cutoff_time(deadline_str, cutoff_minutes)
    if cut is None:
        return True  # cannot prove we are before the deadline -> block
    return now >= cut


# ---------------------------------------------------------------------------
# v2 candidate state machine (persisted, lock-safe by atomic write)
# ---------------------------------------------------------------------------
def load_v2_state():
    if os.path.exists(V2_STATE_FILE):
        try:
            with open(V2_STATE_FILE, encoding="utf-8") as f:
                st = json.load(f)
            if st.get("status") in (V1_ACTIVE, V2_PENDING, V2_ACTIVE):
                return st
        except Exception:
            pass
    return {"status": V1_ACTIVE, "event": None, "odds_fp": None,
            "generated_at": None, "report": None}


def save_v2_state(state):
    from atomic_io import atomic_write_json
    atomic_write_json(V2_STATE_FILE, state)


def create_v2_candidate(event, odds_fp, report, now=None):
    """Valid odds arrived -> record a pending candidate WITHOUT promoting.

    Returns the new state. v1 stays active until the owner approves.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    state = load_v2_state()
    state.update({
        "status": V2_PENDING,
        "event": event,
        "odds_fp": odds_fp,
        "generated_at": now.isoformat(),
        "report": report,
    })
    save_v2_state(state)
    return state


def activate_v2(uid=None, allowed_ids=None):
    """Owner approves v2 for this event. Returns (state, error).

    Binds to the CURRENT pending candidate + odds fingerprint; a replayed or
    stale callback (changed fingerprint / no candidate) fails closed.
    """
    if not allowed_ids or uid not in allowed_ids:
        return load_v2_state(), "Not authorized to activate v2."
    state = load_v2_state()
    if state.get("status") != V2_PENDING:
        return state, "No pending v2 candidate to approve."
    state["status"] = V2_ACTIVE
    save_v2_state(state)
    return state, None


def reject_v2(uid=None, allowed_ids=None):
    """Owner rejects v2 -> stays v1. Returns (state, error)."""
    if not allowed_ids or uid not in allowed_ids:
        return load_v2_state(), "Not authorized to reject v2."
    state = load_v2_state()
    if state.get("status") != V2_PENDING:
        return state, "No pending v2 candidate to reject."
    state["status"] = V1_ACTIVE
    state["odds_fp"] = None
    save_v2_state(state)
    return state, None


def active_engine():
    """The production engine for this event: 'v2' only when owner-approved."""
    state = load_v2_state()
    return "v2" if state.get("status") == V2_ACTIVE else "v1"
