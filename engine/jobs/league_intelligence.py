"""Refresh adaptive intelligence for all configured FPL leagues.

This public/read-only job is safe to run on every automation cycle.  It keeps
all standings lightweight, selects at most a configured deep cohort, fetches
only that cohort's histories and post-deadline picks, and writes immutable plus
latest snapshots for the recommendation card.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "model"))

from atomic_io import atomic_write_json  # noqa: E402
from fpl_client import FPLClient  # noqa: E402
import manager_sharpness  # noqa: E402
from league_signals import (  # noqa: E402
    cohort_live_swing,
    manager_activity,
    market_signals,
    monthly_totals,
    set_piece_signals,
    simulate_prize_probabilities,
    transfer_consensus,
)
from opponent_intelligence import (  # noqa: E402
    exposure_from_picks,
    load_scout_priors,
    select_deep_cohort,
    validate_locked_picks,
)
from prize_strategy import calculate_prize_status, load_prize_config, prize_mode  # noqa: E402


OUT_DIR = os.path.join(BASE, "data", "processed", "league_intelligence")
LATEST_FILE = os.path.join(OUT_DIR, "latest.json")
REGISTRY_DIR = os.path.join(OUT_DIR, "registry")
MONTHLY_LEDGER_FILE = os.path.join(OUT_DIR, "monthly_ledger.json")


def load_settings():
    with open(os.path.join(BASE, "config", "settings.json"), encoding="utf-8") as handle:
        return json.load(handle)


def fetch_league(client, league_id, max_pages=200):
    """Fetch every standings/new-entry page with repeat detection."""
    members = {}
    meta = {}
    complete = True
    seen_signatures = set()
    page = 1
    while page <= max_pages:
        payload = client.get_json(
            f"leagues-classic/{league_id}/standings/"
            f"?page_standings={page}&page_new_entries={page}"
        )
        meta = payload.get("league", meta) or meta
        standings = payload.get("standings", {}) or {}
        new_entries = payload.get("new_entries", {}) or {}
        srows = standings.get("results", []) or []
        nrows = new_entries.get("results", []) or []
        signature = (
            tuple(int(r["entry"]) for r in srows if r.get("entry")),
            tuple(int(r["entry"]) for r in nrows if r.get("entry")),
        )
        if signature in seen_signatures and (srows or nrows):
            complete = False
            break
        seen_signatures.add(signature)
        for row in nrows:
            entry = int(row["entry"])
            members[entry] = {
                "entry": entry,
                "entry_name": row.get("entry_name"),
                "player_name": " ".join(p for p in [row.get("player_first_name"), row.get("player_last_name")] if p),
                "rank": None,
                "last_rank": None,
                "total": None,
                "event_total": None,
                "league_id": int(league_id),
                "source_state": "new_entries",
            }
        for row in srows:
            entry = int(row["entry"])
            current = members.get(entry, {})
            current.update({
                "entry": entry,
                "entry_name": row.get("entry_name") or current.get("entry_name"),
                "player_name": row.get("player_name") or current.get("player_name"),
                "rank": row.get("rank"),
                "last_rank": row.get("last_rank"),
                "total": row.get("total"),
                "event_total": row.get("event_total"),
                "league_id": int(league_id),
                "source_state": "standings",
            })
            members[entry] = current
        if not standings.get("has_next") and not new_entries.get("has_next"):
            break
        page += 1
    if page > max_pages:
        complete = False
    return {
        "league_id": int(league_id),
        "league_name": meta.get("name"),
        "complete": complete,
        "pages": page,
        "members": list(members.values()),
    }


def event_context(bootstrap, now):
    finished = [int(e["id"]) for e in bootstrap.get("events", []) if e.get("finished")]
    completed = max(finished, default=0)
    locked = []
    for event in bootstrap.get("events", []):
        try:
            deadline = dt.datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
            if now >= deadline + dt.timedelta(minutes=5):
                locked.append(int(event["id"]))
        except Exception:
            continue
    exposure_event = max(locked, default=0)
    next_event = next((int(e["id"]) for e in bootstrap.get("events", []) if not e.get("finished") and int(e["id"]) > completed), completed + 1)
    return completed, exposure_event, next_event


def event_deadlines(bootstrap):
    result = {}
    for event in bootstrap.get("events", []) or []:
        try:
            result[int(event["id"])] = dt.datetime.fromisoformat(str(event["deadline_time"]).replace("Z", "+00:00"))
        except Exception:
            continue
    return result


def _load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {} if default is None else default


def membership_hash(leagues):
    memberships = sorted(
        (int(league["league_id"]), int(row["entry"]))
        for league in leagues for row in league.get("members", []) or []
    )
    return hashlib.sha256(json.dumps(memberships, separators=(",", ":")).encode()).hexdigest()


def registry_paths(event):
    return (
        os.path.join(REGISTRY_DIR, f"gw{int(event):02d}_provisional.json"),
        os.path.join(REGISTRY_DIR, f"gw{int(event):02d}_final.json"),
    )


def apply_deadline_registry(leagues, event, now, deadline):
    """Continuously refresh entrants, then freeze exact membership after lock."""
    os.makedirs(REGISTRY_DIR, exist_ok=True)
    provisional_path, final_path = registry_paths(event)
    compact = {
        "schema_version": 1,
        "event": int(event),
        "captured_at": now.isoformat(),
        "deadline": deadline.isoformat() if deadline else None,
        "membership_hash": membership_hash(leagues),
        "leagues": [
            {
                "league_id": int(league["league_id"]),
                "league_name": league.get("league_name"),
                "entries": sorted(int(row["entry"]) for row in league.get("members", []) or []),
            }
            for league in leagues
        ],
    }
    locked = bool(deadline and now >= deadline + dt.timedelta(minutes=5))
    if not locked:
        compact["status"] = "provisional"
        atomic_write_json(provisional_path, compact)
        return leagues, {"status": "provisional", "membership_hash": compact["membership_hash"], "finalized_at": None}
    final = _load_json(final_path, default=None)
    if not final:
        compact["status"] = "final"
        compact["finalized_at"] = now.isoformat()
        atomic_write_json(final_path, compact)
        final = compact
    allowed = {int(item["league_id"]): {int(e) for e in item.get("entries", [])} for item in final.get("leagues", [])}
    filtered = []
    for league in leagues:
        league_copy = dict(league)
        ids = allowed.get(int(league["league_id"]), set())
        league_copy["members"] = [row for row in league.get("members", []) if int(row["entry"]) in ids]
        filtered.append(league_copy)
    return filtered, {
        "status": "final",
        "membership_hash": final.get("membership_hash"),
        "finalized_at": final.get("finalized_at"),
        "final_path": os.path.relpath(final_path, BASE).replace("\\", "/"),
    }


def update_monthly_ledger(ledger, rows, event, event_meta):
    """Idempotently store per-event scores once FPL marks data checked."""
    if not event or not event_meta or not event_meta.get("finished") or not event_meta.get("data_checked"):
        return ledger
    try:
        month = dt.datetime.fromisoformat(str(event_meta["deadline_time"]).replace("Z", "+00:00")).strftime("%Y-%m")
    except Exception:
        return ledger
    month_data = ledger.setdefault(month, {})
    for row in rows:
        if row.get("event_total") is None:
            continue
        league = month_data.setdefault(str(int(row["league_id"])), {})
        event_scores = league.setdefault(str(int(event)), {})
        event_scores[str(int(row["entry"]))] = float(row["event_total"])
    return ledger


def normalized_hash(payload):
    picks = sorted(
        ({k: p.get(k) for k in ("element", "position", "multiplier", "is_captain", "is_vice_captain")}
         for p in payload.get("picks", []) or []),
        key=lambda p: (p.get("position") or 99, p.get("element") or 0),
    )
    return hashlib.sha256(json.dumps(picks, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def closest_reference_gap(rows, cohort, priors, our_entry, primary_league):
    own = next((r for r in rows if int(r["league_id"]) == primary_league and int(r["entry"]) == our_entry), None)
    if not own or own.get("total") is None:
        return None, None
    cohort_ids = {int(c["entry"]) for c in cohort}
    candidates = []
    for row in rows:
        entry = int(row["entry"])
        if int(row["league_id"]) != primary_league or entry not in cohort_ids or row.get("total") is None:
            continue
        prior = priors.get(entry, {})
        if prior.get("tier") not in {"S", "A"}:
            continue
        gap = float(row["total"]) - float(own["total"])
        candidates.append((abs(gap), gap, entry, row.get("entry_name")))
    if not candidates:
        return None, None
    _, gap, entry, name = min(candidates)
    return gap, {"entry": entry, "team_name": name, "gap": round(gap, 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notifications-disabled", action="store_true", default=True)
    parser.add_argument("--finalize-if-due", action="store_true",
                        help="No-op outside the two-hour post-deadline registry/picks finalization window")
    args = parser.parse_args()

    settings = load_settings()
    cfg = settings.get("league_intelligence", {}) or {}
    if not cfg.get("enabled", True):
        print("league intelligence disabled")
        return
    registry_path = os.path.join(BASE, "config", "league_registry.json")
    registry_ids = []
    try:
        with open(registry_path, encoding="utf-8") as source:
            registry = json.load(source)
        registry_ids = [int(row["league_id"]) for row in registry.get("leagues", [])
                        if row.get("status", "active") == "active"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        registry_ids = []
    league_ids = registry_ids or [int(x) for x in cfg.get("league_ids", [])]
    if not league_ids:
        print("no league IDs configured")
        return
    our_entry = int(settings["team_id"])
    # All intelligence endpoints are public. An explicit anonymous client
    # prevents stale cookies/bearer headers from turning public reads into 401s.
    client = FPLClient(session_data={})
    now = dt.datetime.now(dt.timezone.utc)
    bootstrap = client.get_json("bootstrap-static/")
    completed_gws, exposure_event, next_event = event_context(bootstrap, now)
    deadlines = event_deadlines(bootstrap)
    target_deadline = deadlines.get(next_event)
    previous = _load_json(LATEST_FILE, default={})
    if args.finalize_if_due:
        due = target_deadline and target_deadline + dt.timedelta(minutes=5) <= now <= target_deadline + dt.timedelta(hours=2)
        attempts = int(previous.get("finalization_attempts", 0) or 0) if int(previous.get("event", -1)) == next_event else 0
        if not due or attempts >= int(cfg.get("finalization_max_attempts", 3)):
            return
    element_names = {int(e["id"]): e.get("web_name", str(e["id"])) for e in bootstrap.get("elements", [])}

    leagues = [fetch_league(client, league_id, int(cfg.get("max_pages", 200))) for league_id in league_ids]
    leagues, registry = apply_deadline_registry(leagues, next_event, now, target_deadline)
    rows = [row for league in leagues for row in league["members"]]
    priors = load_scout_priors(cfg.get("scout_file") or None)
    cohort = select_deep_cohort(
        rows, priors, our_entry,
        max_size=int(cfg.get("deep_cohort_cap", 40)),
        top_per_league=int(cfg.get("top_per_league", 6)),
        sharp_slots=int(cfg.get("sharp_slots", 8)),
        proximity_slots=int(cfg.get("proximity_slots", 8)),
        pinned=cfg.get("pinned_entries", []),
    )

    previous_hashes = previous.get("pick_hashes", {}) if int(previous.get("exposure_event", -1)) == exposure_event else {}

    history_payloads = {}
    transfers_by_entry = {}
    for opponent in cohort:
        entry = int(opponent["entry"])
        try:
            history = client.entry_history(entry)
            history_payloads[entry] = history
            current = history.get("current", []) or []
            scored = manager_sharpness.score_manager(current, prior=float(opponent["historical_score"]))
            opponent["live_sharpness"] = scored["score"]
            opponent["live_confidence"] = scored["confidence"]
            opponent["gws_evaluated"] = scored["gws_evaluated"]
            transfers = client.entry_transfers(entry) if exposure_event > 0 else []
            transfers_by_entry[entry] = transfers
            opponent["activity"] = manager_activity(history, transfers, deadlines)
        except Exception as exc:
            opponent["history_error"] = repr(exc)[:160]

    picks_by_entry = {}
    pick_hashes = {}
    pick_trust = {}
    if exposure_event > 0:
        for opponent in cohort:
            entry = int(opponent["entry"])
            try:
                payload = client.entry_picks(entry, exposure_event)
                if not validate_locked_picks(payload, exposure_event):
                    pick_trust[str(entry)] = "invalid"
                    continue
                digest = normalized_hash(payload)
                pick_hashes[str(entry)] = digest
                stable = previous_hashes.get(str(entry)) == digest
                pick_trust[str(entry)] = "trusted" if stable else "pending_stability"
                if stable:
                    picks_by_entry[entry] = payload
            except Exception as exc:
                pick_trust[str(entry)] = f"unavailable:{repr(exc)[:80]}"

    exposure = exposure_from_picks(cohort, picks_by_entry, element_names)
    transfer_moves = transfer_consensus(cohort, transfers_by_entry, exposure_event, element_names) if exposure_event else []
    prize_config = load_prize_config(cfg.get("prize_config") or None)
    prize_leagues = [p for p in prize_config.get("leagues", []) if int(p.get("league_id", -1)) in league_ids]
    prize_status = []
    remaining_gws = max(1, 38 - completed_gws)
    for prize_league in prize_leagues:
        status = calculate_prize_status(rows, our_entry, prize_league, completed_gws)
        status["monthly"] = prize_league.get("monthly")
        status["active_special"] = (prize_league.get("special_gameweeks", {}) or {}).get(str(next_event))
        league_rows = [row for row in rows if int(row["league_id"]) == int(prize_league["league_id"])]
        status["probability"] = simulate_prize_probabilities(
            league_rows, our_entry, prize_league.get("overall", []), remaining_gws,
            simulations=int(cfg.get("prize_simulations", 1000)),
            sigma_per_gw=float(cfg.get("score_sigma_per_gw", 14.0)),
            seed=int(next_event) * 100000 + int(prize_league["league_id"]),
        )
        prize_status.append(status)

    monthly_ledger = _load_json(MONTHLY_LEDGER_FILE, default={})
    completed_meta = next((event for event in bootstrap.get("events", []) if int(event.get("id", -1)) == completed_gws), None)
    monthly_ledger = update_monthly_ledger(monthly_ledger, rows, completed_gws, completed_meta)
    os.makedirs(OUT_DIR, exist_ok=True)
    atomic_write_json(MONTHLY_LEDGER_FILE, monthly_ledger)
    month_key = (target_deadline or now).strftime("%Y-%m")
    monthly_status = [
        monthly_totals(rows, monthly_ledger, month_key, int(prize["league_id"]), our_entry, prize.get("monthly"))
        for prize in prize_leagues if prize.get("monthly")
    ]
    primary_league = int(cfg.get("primary_league", league_ids[0]))
    reference_gap, reference = closest_reference_gap(rows, cohort, priors, our_entry, primary_league)
    mode = prize_mode(prize_status, completed_gws)
    mode["primary_league"] = primary_league
    mode["reference_rival"] = reference
    mode["reference_gap"] = reference_gap

    live_swing = None
    if exposure_event > 0:
        try:
            our_picks = client.entry_picks(our_entry, exposure_event)
            live_payload = client.get_json(f"event/{exposure_event}/live/")
            live_swing = cohort_live_swing(our_picks, picks_by_entry, live_payload)
        except Exception:
            live_swing = None

    state = {
        "schema_version": 2,
        "run_id": os.getenv("FPL_RUN_ID") or None,
        "as_of": now.isoformat(),
        "event": next_event,
        "completed_gws": completed_gws,
        "exposure_event": exposure_event,
        "our_entry": our_entry,
        "league_ids": league_ids,
        "complete": all(league["complete"] for league in leagues),
        "registry": registry,
        "finalization_attempts": (
            int(previous.get("finalization_attempts", 0) or 0) + 1
            if args.finalize_if_due and int(previous.get("event", -1)) == next_event
            else (1 if args.finalize_if_due else int(previous.get("finalization_attempts", 0) or 0))
        ),
        "leagues": [{k: v for k, v in league.items() if k != "members"} | {"member_count": len(league["members"])} for league in leagues],
        "standings": rows,
        "cohort": cohort,
        "cohort_count": len(cohort),
        "pick_hashes": pick_hashes,
        "pick_trust": pick_trust,
        "trusted_pick_count": len(picks_by_entry),
        "player_exposure": exposure,
        "transfer_consensus": transfer_moves[:20],
        "market_signals": market_signals(bootstrap.get("elements", []), limit=int(cfg.get("market_signal_limit", 20))),
        "set_piece_signals": set_piece_signals(bootstrap.get("elements", [])),
        "monthly_status": monthly_status,
        "live_swing": live_swing,
        "prize_status": prize_status,
        "mode": mode,
        "source": "https://fantasy.premierleague.com/api/",
    }
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir = os.path.join(OUT_DIR, f"gw{next_event:02d}")
    os.makedirs(snapshot_dir, exist_ok=True)
    atomic_write_json(os.path.join(snapshot_dir, f"intelligence_{stamp}.json"), state)
    atomic_write_json(LATEST_FILE, state)
    if not args.finalize_if_due:
        print(
            f"league intelligence: {len(rows)} memberships, {len(cohort)} deep opponents, "
            f"{len(picks_by_entry)} trusted picks, registry={registry['status']}, mode={mode['mode']}"
        )


if __name__ == "__main__":
    main()
