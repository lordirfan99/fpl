"""
FPL Autopilot - pre-deadline weekly run (the full pipeline).

1. Pull bootstrap + fixtures + my-team
2. Compute calibrated component xPts for every player over three gameweeks
3. Joint horizon MILP -> transfers, banked FTs, legal XI, bench and captain
5. Write pending_plan.json (approval payload) + predictions snapshot
6. Send the approval card to Telegram (inline Approve/Reject)

Idempotent: safe to run multiple times before a deadline - regenerates the
plan each time. Run: .venv/Scripts/python.exe jobs/pre_deadline_run.py

All live decisions use the odds-free Competitive V4 projection and V4.1
horizon optimizer. Legacy V1/V2/V3 modules remain offline research artifacts
and cannot be selected by this job.
  KEEP/EXCLUDE player preferences (config/player_prefs.json) are enforced:
        keep -> solver may not transfer the player out;
        exclude -> player cannot be transferred in nor selected in the XI.
"""
import json
import os
import argparse
import hashlib
import sys
import datetime
import uuid
import urllib.request
import urllib.parse

import telegram_notify
from project_paths import resolve_project_root

BASE = str(resolve_project_root(__file__))
sys.path.insert(0, os.path.join(BASE, "optimizer"))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "execution"))

from squad_solver import LINEUP_MAX, SQUAD_QUOTA, solve_lineup, solve_squad  # noqa: E402
from plan_validation import validate_plan  # noqa: E402
from proposal_binding import (  # noqa: E402
    canonical_plan_hash, input_fingerprint, settings_fingerprint)
from transfer_solver import squad_horizon_utility  # noqa: E402
from horizon_milp import optimize_horizon  # noqa: E402
from fpl_client import FPLClient  # noqa: E402
from atomic_io import atomic_write_json  # noqa: E402
from competitive_v4_client import CompetitiveV4Error, align_current_squad, fetch_competitive_v4  # noqa: E402
from decision_explanation import build_decision_summary  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
POS_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
PREFS_FILE = os.path.join(BASE, "config", "player_prefs.json")


def competitive_notification_signature(plan):
    """Semantic league context that must invalidate Telegram card dedup."""
    competitive = plan.get("competitive") or {}
    gate = competitive.get("template_gate") or {}
    return {
        "context_status": competitive.get("context_status", "ready"),
        "phase": competitive.get("phase"),
        "alignment": competitive.get("alignment"),
        "target_alignment": competitive.get("target_alignment"),
        "template_formation": competitive.get("template_formation"),
        "decision": gate.get("decision"),
        "differential_allowed": gate.get("differential_allowed"),
        "candidate_gate_applied": ((competitive.get("candidate_gate") or {}).get("applied")),
        "template_core": [row.get("element") for row in
                          (competitive.get("elite_template") or [])[:5]],
        "captain_consensus": [row.get("element") for row in
                              (competitive.get("captain_consensus") or [])[:3]],
        "decision_action": ((plan.get("decision_summary") or {}).get("recommended_action")),
        "roadmap": [
            [row.get("gw"), row.get("action"),
             [[move.get("out"), move.get("in")] for move in ((row.get("route") or {}).get("moves") or [])]]
            for row in ((plan.get("decision_summary") or {}).get("roadmap") or [])
        ],
    }


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


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


def send_telegram_card(text, chat_id, token):
    return telegram_notify.send_message(
        token, chat_id, text, parse_mode="HTML",
        reply_markup={"inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": "approve"},
            {"text": "❌ Reject", "callback_data": "reject"},
        ]]},
        log_prefix="  ",
    )


def fdr_maps(fixtures, gw_ids):
    """{(gw, team_id): [FDR per fixture]} for the requested GWs.

    P0.4: values are LISTS (one entry per fixture) so a double-gameweek
    player keeps BOTH fixture difficulties instead of the second overwriting
    the first.
    """
    out = {}
    for f in fixtures:
        if f.get("event") in gw_ids:
            out.setdefault((f["event"], f["team_h"]), []).append(f["team_h_difficulty"])
            out.setdefault((f["event"], f["team_a"]), []).append(f["team_a_difficulty"])
    return out


def detected_transfer_chip(team, gw):
    """Return a live, already-active transfer chip for this GW, else None.

    `my-team.active_chip` is unreliable once a chip is selected in the official
    UI; the account payload records it under `chips[].played_by_entry` /
    `status_for_entry`. Only Wildcard and Free Hit take the full-squad rebuild
    route, and only for the target gameweek.
    """
    for chip in team.get("chips") or []:
        name = str(chip.get("name") or chip.get("chip_type") or "").lower()
        played = {int(event) for event in (chip.get("played_by_entry") or [])
                  if str(event).isdigit()}
        active = str(chip.get("status_for_entry") or "").lower() == "active"
        if (name in {"wildcard", "freehit"} and active
                and (int(gw) in played or bool(chip.get("is_pending")))):
            return name
    return None


def account_inputs_verified(team):
    picks = team.get("picks") or []
    transfers = team.get("transfers") or {}
    return (len(picks) == 15 and len({p.get("element") for p in picks}) == 15
            and transfers.get("bank") is not None
            and all(p.get("selling_price") is not None for p in picks)
            and (transfers.get("limit") is not None or transfers.get("status") == "unlimited"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notifications-disabled", action="store_true",
                        help="Build and persist the plan without sending a Telegram card")
    parser.add_argument("--force-notify", action="store_true",
                        help="Send the Telegram card even when the prior plan is unchanged")
    parser.add_argument("--verify-inputs-only", action="store_true",
                        help="Read account and league inputs, report provenance, then exit without saving a plan or sending a card")
    args = parser.parse_args()
    settings = load_settings()
    creds = load_creds()
    team_id = settings["team_id"]
    prefs = load_player_prefs()

    client = FPLClient()
    bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    fixtures = fetch("https://fantasy.premierleague.com/api/fixtures/")
    team = client.my_team(team_id)
    if not args.verify_inputs_only and not account_inputs_verified(team):
        raise RuntimeError("Current account inputs incomplete; no personal plan can be saved")

    # --- determine GW context ---
    now = datetime.datetime.now(datetime.timezone.utc)
    run_id = os.getenv("FPL_RUN_ID") or (
        "v41-" + now.strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    )
    refresh_failures = [item for item in os.getenv("FPL_REFRESH_FAILURES", "").split(",") if item]
    next_gw = None
    for ev in bootstrap["events"]:
        dl = datetime.datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
        if not ev["finished"] and dl > now:
            next_gw = ev
            break
    if not next_gw:
        print("No upcoming gameweek found - season over?")
        return
    gw = next_gw["id"]
    gw_so_far = gw - 1
    active_transfer_chip = detected_transfer_chip(team, gw)
    print(f"=== PRE-DEADLINE RUN: GW{gw} (deadline {next_gw['deadline_time']}) ===")
    if active_transfer_chip:
        print(f"live FPL chip detected: {active_transfer_chip} for GW{gw} — full 15-player rebuild")

    # --- xPts for all players: next GW + horizon ---
    gw_ids = list(range(gw, min(gw + 3, 39)))
    competitive_context = None
    competitive_error = None
    try:
        competitive_cfg = settings.get("competitive_v4", {}) or {}
        # Rival picks belong to the last public GW. The client uses that GW as
        # a validation constraint, while allowing the API to select a fresh
        # capture instead of pinning an old finalized file.
        context_gw = max(1, gw - 1)
        competitive_context = fetch_competitive_v4(
            int(competitive_cfg.get("league_id", 58005)),
            context_gw,
            require_executable_plan=False,
        )
        # An owner-configured limit may be stricter, never looser than the API
        # freshness contract. The client also validates the actual timestamp.
        max_age = min(12.0, float(competitive_cfg.get("max_snapshot_age_hours", 12.0)))
        freshness = competitive_context["meta"].get("freshness_hours")
        snap_gw = competitive_context["meta"].get("snapshot_gameweek")
        if freshness is not None and float(freshness) > max_age:
            raise CompetitiveV4Error(
                f"V4 context is {freshness}h old; max {max_age:g}h")
        if snap_gw is not None and int(snap_gw) < context_gw:
            raise CompetitiveV4Error(
                f"V4 context is GW{snap_gw}, expected GW{context_gw}")
    except CompetitiveV4Error as error:
        competitive_context = None
        competitive_error = error

    if args.verify_inputs_only:
        picks = team.get("picks") or []
        transfers = team.get("transfers") or {}
        account_ok = account_inputs_verified(team)
        print(json.dumps({
            "verification_only": True, "target_gameweek": gw,
            "account": {"source": "authenticated_my_team", "verified": account_ok,
                        "fetched_at": now.isoformat(), "squad_count": len(picks),
                        "bank_known": transfers.get("bank") is not None},
            "league": (competitive_context or {}).get("freshness"),
            "error": str(competitive_error) if competitive_error else None,
            "plan_saved": False, "card_sent": False,
        }))
        if not account_ok or not competitive_context:
            raise SystemExit(1)
        return

    # Current-season league intelligence snapshot (used to build the elite
    # template from THIS season's evidence instead of preseason scout priors).
    li_cfg = settings.get("league_intelligence", {}) or {}
    league_state = {}
    try:
        from opponent_intelligence import load_latest_state
        league_state = load_latest_state(
            max_age_hours=float(li_cfg.get("state_max_age_hours", 36))) or {}
    except Exception:
        league_state = {}

    elements = bootstrap["elements"]
    players = []
    calibration = {}
    sys.path.insert(0, os.path.join(BASE, "model"))
    from fixture_engine import fixtures_by_team_gw
    from v4_projection import captain_rankings, project_player, select_vice
    from calibration import bias_adjustment, calibration_summary, uncertainty_scale
    fmap = fixtures_by_team_gw(fixtures, gw_ids)
    calibration = calibration_summary(
        os.path.join(BASE, "data", "processed", "residuals.csv"))
    empirical_uncertainty = uncertainty_scale(calibration, min_rows=100)
    projection_version = "competitive-v4.0"
    registry_path = os.path.join(BASE, "data", "processed", "model_registry.json")
    try:
        with open(registry_path, encoding="utf-8") as registry_handle:
            registry = json.load(registry_handle)
        if registry.get("active_projection") == "competitive-v4.2":
            projection_version = "competitive-v4.2"
    except (OSError, ValueError, TypeError):
        pass
    v42_history = v42_strengths = v42_calibration = None
    if projection_version == "competitive-v4.2":
        from calibration_v42 import calibration_summary as calibration_summary_v42
        from feature_store_v42 import history_by_player, load_event_history, team_strengths
        from v42_projection import project_player_v42
        v42_history = history_by_player(load_event_history(os.path.join(
            BASE, "data", "processed", "player_event_history.jsonl")), gw)
        v42_strengths = team_strengths(fixtures, gw)
        v42_calibration = calibration_summary_v42(os.path.join(
            BASE, "data", "processed", "v42_residuals.csv"))
    decision_calibration = v42_calibration if projection_version == "competitive-v4.2" else calibration
    # Rolling per-position bias correction: subtract the mean (predicted-actual)
    # residual from the point estimate. Self-activating (0 until enough data),
    # shrunk by sample size and hard-capped in calibration.bias_adjustment.
    bias_by_pos = {p: bias_adjustment(decision_calibration, p) for p in ("GKP", "DEF", "MID", "FWD")}
    _POINT_KEYS = ("xpts", "xpts_horizon", "expected_horizon",
                   "risk_adjusted_xpts", "xpts_floor", "xpts_upside")
    print(f"projection={projection_version} | V4.1 calibration: {calibration} | "
          f"uncertainty x{empirical_uncertainty:.2f} | bias adj {bias_by_pos}")
    for el in elements:
        if not el.get("can_select"):
            continue
        pos = POS_MAP.get(el.get("element_type"), "MID")
        if projection_version == "competitive-v4.2":
            candidate = project_player_v42(
                el, fmap, gw_ids, v42_history, v42_strengths, v42_calibration)
            forecast = {
                "xpts": candidate["mean"], "xpts_horizon": candidate["expected_horizon"],
                "xpts_floor": candidate["floor"], "xpts_upside": candidate["upside"],
                "xpts_variance": candidate["variance"], "p_start": candidate["p_start"],
                "expected_minutes": candidate["expected_minutes"],
                "confidence": candidate["confidence"], "uncertainty_multiplier": 1.0,
                "risk_adjusted_xpts": max(0.0, candidate["mean"] -
                                           0.25 * candidate["variance"] ** 0.5),
                "xpts_by_gw": candidate["xpts_by_gw"],
                "variance_by_gw": candidate["variance_by_gw"],
                "components": candidate["components"],
                "p_dnp": candidate["p_dnp"], "p_1_59": candidate["p_1_59"],
                "p_60_plus": candidate["p_60_plus"],
                "degraded_reasons": candidate["degraded_reasons"],
            }
        else:
            forecast = project_player(
                el, fmap, gw_so_far, gw_ids,
                uncertainty_multiplier=empirical_uncertainty)

        adj = bias_by_pos.get(pos, 0.0)
        if adj:
            for _k in _POINT_KEYS:
                if forecast.get(_k) is not None:
                    forecast[_k] = round(max(0.0, float(forecast[_k]) + adj), 3)
            if isinstance(forecast.get("xpts_by_gw"), list):
                forecast["xpts_by_gw"] = [round(max(0.0, float(v) + adj), 3)
                                          for v in forecast["xpts_by_gw"]]

        players.append({
            "id": el["id"], "name": el["web_name"], "position": pos,
            "club": el["team"], "cost": int(el["now_cost"]),
            "xpts": forecast["xpts"], "xpts_horizon": forecast["xpts_horizon"],
            "status": el.get("status"), "cop": el.get("chance_of_playing_next_round"),
            "news": el.get("news"), "xpts_floor": forecast["xpts_floor"],
            "xpts_upside": forecast["xpts_upside"],
            "xpts_variance": forecast["xpts_variance"], "p_start": forecast["p_start"],
            "expected_minutes": forecast["expected_minutes"],
            "confidence": forecast["confidence"],
            "uncertainty_multiplier": forecast["uncertainty_multiplier"],
            "risk_adjusted_xpts": forecast["risk_adjusted_xpts"],
            "xpts_by_gw": forecast["xpts_by_gw"],
            "variance_by_gw": forecast["variance_by_gw"],
            "components": forecast["components"],
            "p_dnp": forecast.get("p_dnp"), "p_1_59": forecast.get("p_1_59"),
            "p_60_plus": forecast.get("p_60_plus"),
            "degraded_reasons": forecast.get("degraded_reasons", []),
        })

    # --- bonus layer (Sol P5: replacement-style additive correction) ---
    # Flag-gated via settings.json `bonus_model_enabled`. When on, loads the
    # fixture-sim E[bonus] map and applies delta = E[bonus]_2026 - embedded.
    # The ML row-wise model stays diagnostics-only (failed the 5% gate).
    bonus_note = None
    bonus_enabled = bool(settings.get("bonus_model_enabled", False))
    if bonus_enabled:
        try:
            sys.path.insert(0, os.path.join(BASE, "model"))
            import bonus_layer
            bonus_map = bonus_layer.load_bonus_file(gw)
            if bonus_map:
                for p in players:
                    # mp = the play-probability already used for this player
                    # (recomputed from bootstrap below; default neutral 0.5)
                    mp = 0.5
                    try:
                        el = next(e for e in bootstrap["elements"] if e["id"] == p["id"])
                        cop = el.get("chance_of_playing_next_round")
                        news = (el.get("news") or "").strip()
                        mins = float(el.get("minutes", 0) or 0)
                        mp = min(0.92, 0.3 + 0.6 * (mins / max(gw_so_far * 90.0, 1.0)))
                        if (cop is not None and cop < 75) or news:
                            mp *= 0.5
                    except Exception:
                        pass
                    adj = bonus_layer.apply_bonus(p, mp, bonus_map, enabled=True)
                    if adj is not p:
                        p.update(adj)
                bonus_note = f"⚠️ Bonus layer ACTIVE (E[bonus] 2026/27 fixture-sim, {len(bonus_map)} players)"
                print(bonus_note)
            else:
                bonus_note = "⚠️ Bonus layer enabled but no gw_bonus.json found - v2 unchanged (safe fallback)."
                print(bonus_note)
        except Exception as e:
            bonus_note = f"⚠️ Bonus layer failed ({repr(e)[:80]}) - v2 unchanged."
            print(bonus_note)

    # --- current squad state ---
    els_by_id = {p["id"]: p for p in players}
    cur_picks = team.get("picks", [])
    squad = []
    for p in cur_picks:
        base = els_by_id.get(p["element"])
        if not base:
            continue
        squad.append({
            "id": base["id"], "name": base["name"], "position": base["position"],
            "club": base["club"], "cost": base["cost"],
            "xpts": base["xpts"], "xpts_horizon": base["xpts_horizon"],
            "xpts_by_gw": base.get("xpts_by_gw", []),
            "variance_by_gw": base.get("variance_by_gw", []),
            "xpts_floor": base.get("xpts_floor"),
            "xpts_upside": base.get("xpts_upside"),
            "xpts_variance": base.get("xpts_variance", 0),
            "p_start": base.get("p_start"),
            "expected_minutes": base.get("expected_minutes"),
            "uncertainty_multiplier": base.get("uncertainty_multiplier"),
            "risk_adjusted_xpts": base.get("risk_adjusted_xpts", base["xpts"]),
            "confidence": base.get("confidence"),
            "selling_price": int(p.get("selling_price") or base["cost"]),
            "purchase_price": int(p.get("purchase_price") or base["cost"]),
            "status": base.get("status"), "cop": base.get("cop"), "news": base.get("news"),
        })
    bank = team.get("transfers", {}).get("bank", 0)
    free_transfers = 1
    free_transfers_synced = False
    if team.get("transfers", {}).get("status") == "unlimited":
        free_transfers = 99
        free_transfers_synced = True
    else:
        tr = team.get("transfers", {})
        # FPL reports the number available *before* this week's actions via
        # ``limit`` and the number already used via ``made``.  Preserve zero:
        # after using the only free transfer, another move must be treated as
        # paid (or rejected by the hit guard), never silently made free.
        free_transfers = max(0, (tr.get("limit") or 1) - (tr.get("made") or 0))
        if tr.get("limit") is None:
            free_transfers = 1
        else:
            free_transfers_synced = True

    current_total = sum(p["xpts"] for p in squad)
    current_horizon = sum(p["xpts_horizon"] for p in squad)
    print(f"current squad: {len(squad)} players | GW{gw} xPts {current_total:.1f} | horizon {current_horizon:.1f} | bank {bank} | FTs {free_transfers}")

    # --- keep/exclude preferences (user buttons on the bot) ---
    keep_ids = set(prefs["keep"])
    exclude_ids = set(prefs["exclude"])
    if keep_ids or exclude_ids:
        names = {p["id"]: p["name"] for p in players}
        print("prefs: keep", [names.get(i, i) for i in sorted(keep_ids)],
              "| exclude", [names.get(i, i) for i in sorted(exclude_ids)])

    # --- transfers (candidates exclude "exclude" players; "keep" are protected) ---
    candidates = [p for p in players if p["id"] not in exclude_ids]
    template_candidate_gate = {
        "applied": False, "reason": "Competitive template gate is open or unavailable."
    }
    if competitive_context:
        align_current_squad(competitive_context, {player["id"] for player in squad})
        gate = competitive_context.get("template_gate") or {}
        converge = gate.get("decision") == "CONVERGE_TO_TEMPLATE"
        differential_locked = not bool(gate.get("differential_allowed"))
        template_ids = {
            int(row["element"]) for row in (competitive_context.get("elite_template") or [])
            if row.get("element") is not None
        }
        owned_ids = {player["id"] for player in squad}

        # Owner preference: derive "elite" from THIS season's league standings,
        # not the preseason scout tiers. When league_intelligence has published
        # a current-season template with enough players, it replaces the scout
        # one for the gate (source + alignment recomputed from live evidence).
        template_source = str(li_cfg.get("elite_template_source", "current_season"))
        live_template = (league_state or {}).get("elite_template_live") or {}
        live_ids = {int(p["element"]) for p in (live_template.get("players") or [])
                    if p.get("element") is not None}
        if (competitive_context.get("freshness") or {}).get("source") != "official-fpl-live" and template_source == "current_season" and len(live_ids) >= int(
                li_cfg.get("elite_template_min_players", 6)):
            template_ids = live_ids
            threshold = float(li_cfg.get("elite_template_alignment_threshold", 0.82))
            alignment = len(owned_ids & template_ids) / len(template_ids)
            converge = alignment < threshold
            differential_locked = converge
            gate = {
                "decision": "CONVERGE_TO_TEMPLATE" if converge else "HOLD_TEMPLATE",
                "differential_allowed": not differential_locked,
                "alignment": round(alignment, 3),
                "alignment_threshold": threshold,
                "source": "current_season",
                "elite_manager_count": live_template.get("manager_count"),
            }
            # keep the card / dashboard packet consistent with the decision
            competitive_context["template_gate"] = gate
            competitive_context["elite_template"] = live_template.get("players") or []
            competitive_context["alignment"] = round(alignment * 100.0, 1)
            competitive_context["target_alignment"] = round(threshold * 100.0, 1)
            competitive_context["alignment_source"] = "current_season"
            print(f"elite template: current-season ({len(template_ids)} players, "
                  f"{live_template.get('manager_count')} elite mgrs, "
                  f"alignment {alignment:.0%}/{threshold:.0%})")
        # A live Wildcard / Free Hit is exempt: a full 15-player rebuild is a
        # pure xPts squad optimisation over the WHOLE pool. Restricting it to
        # the elite template + current 15 would force the solver to return the
        # squad it already has (0 transfers), keeping any prior mistakes.
        if converge and differential_locked and template_ids and not active_transfer_chip:
            candidates = [
                player for player in candidates
                if player["id"] in owned_ids or player["id"] in template_ids
            ]
            template_candidate_gate = {
                "applied": True,
                "reason": "Differentials locked: transfer-ins restricted to the elite template.",
                "eligible_template_ids": sorted(template_ids),
            }
            print(f"competitive candidate gate: template-only ({len(template_ids)} eligible IDs)")
        elif converge and differential_locked and template_ids and active_transfer_chip:
            print(f"competitive candidate gate: SKIPPED for {active_transfer_chip} "
                  f"— full pool for the 15-player rebuild")
    if (refresh_failures or not competitive_context) and not active_transfer_chip:
        # The current official account can still be optimized for a legal
        # lineup, but a partially refreshed run may not authorize transfers.
        # A live Wildcard/Free Hit is exempt: the rebuild is a pure xPts squad
        # optimisation that does not consume competitive/league context.
        owned_ids = {player["id"] for player in squad}
        candidates = [player for player in candidates if player["id"] in owned_ids]
        template_candidate_gate = {
            "applied": True,
            "safe_mode": True,
            "reason": "Competitive refresh incomplete: transfers locked; lineup-only safe mode.",
            "refresh_failures": refresh_failures or [str(competitive_error or "competitive context unavailable")],
        }
    candidate_ids = {player["id"] for player in candidates}
    paid_transfer_min_gws = int(settings.get("v4_paid_transfer_min_gws", 3))
    paid_transfers_calibrated = gw_so_far >= paid_transfer_min_gws
    transfers = []
    first_week = None
    final_squad = None
    current_utility = squad_horizon_utility(
        squad, float(settings.get("v4_transfer_risk_penalty", 0.25)),
        float(settings.get("v4_bench_depth_weight", 0.08)))
    # Owner preference: cap starting defenders so a 2-game clean-sheet spike
    # can't lock the rebuild into a 5-4-1. Default 5 = FPL max (no change).
    max_starting_def = int(settings.get("v4_max_starting_defenders", 5))
    rebuild_lineup_max = {**LINEUP_MAX, "DEF": max(3, min(5, max_starting_def))}
    if active_transfer_chip:
        # A live Wildcard / Free Hit is a different problem: all 15 places may
        # change, with no FT or hit limit. Budget = live selling value + bank,
        # never the £100m ceiling. No competitive/league context is required.
        rebuild_budget = sum(p.get("selling_price", p["cost"]) for p in squad) + bank
        try:
            final_squad = solve_squad(candidates, budget=rebuild_budget,
                                      lineup_max=rebuild_lineup_max)
        except Exception as error:
            print(f"!! {active_transfer_chip} squad solver failed - no plan persisted: {error!r}")
            sys.exit(1)
        final_ids = {p["id"] for p in final_squad}
        missing_keeps = keep_ids - final_ids
        if missing_keeps:
            print(f"!! {active_transfer_chip} rebuild conflicts with keep preference(s): {sorted(missing_keeps)}")
            sys.exit(1)
        squad_ids = {p["id"] for p in squad}
        for pos in SQUAD_QUOTA:
            outs = sorted((p for p in squad if p["position"] == pos and p["id"] not in final_ids),
                          key=lambda p: p["id"])
            ins = sorted((p for p in final_squad if p["position"] == pos and p["id"] not in squad_ids),
                         key=lambda p: p["id"])
            if len(outs) != len(ins):
                print(f"!! {active_transfer_chip} position pairing failed for {pos}")
                sys.exit(1)
            for out, incoming in zip(outs, ins):
                transfers.append({
                    "element_out": out["id"], "element_in": incoming["id"],
                    "out_name": out["name"], "in_name": incoming["name"],
                    "selling_price": int(out.get("selling_price", out["cost"])),
                    "purchase_price": int(incoming["cost"]),
                    "gain": round(incoming["xpts"] - out["xpts"], 1),
                    "gain_gw1": round(incoming["xpts"] - out["xpts"], 1),
                    "package_gain": None, "hit": False,
                    "optimizer": f"{active_transfer_chip}-full-squad-v1",
                })
        new_bank = int(round(rebuild_budget - sum(p["cost"] for p in final_squad)))
        free_transfers = 99
        ft_left = 99
        horizon_plan = {"objective": sum(p["xpts"] for p in final_squad), "weeks": []}
        notes = [f"{active_transfer_chip.title()} active: full 15-player rebuild, no hits"]
    else:
        try:
            horizon_plan = optimize_horizon(
                squad, candidates, bank, free_transfers,
                horizon=len(gw_ids),
                risk_penalty=float(settings.get("v4_transfer_risk_penalty", 0.25)),
                bench_weight=float(settings.get("v4_bench_depth_weight", 0.08)),
                max_transfers_per_gw=int(settings.get("v4_joint_transfer_limit", 2)),
                max_paid_transfers=int(settings.get("v4_max_paid_transfers", 1)),
                paid_transfers_allowed=paid_transfers_calibrated,
                protected=keep_ids, excluded=exclude_ids,
                captain_min_start=float(settings.get("v4_captain_min_start", 0.75)),
                captain_min_minutes=float(settings.get("v4_captain_min_minutes", 65)),
                transfer_friction=float(settings.get("v4_transfer_friction", 0.15)),
            )
        except Exception as error:
            print(f"!! V4.1 HORIZON MILP FAILED - no plan persisted: {error!r}")
            sys.exit(1)
        first_week = horizon_plan["weeks"][0]
        first_hits = int(first_week.get("hits") or 0)
        for index, move in enumerate(first_week.get("transfers") or []):
            transfers.append({
                **move,
                "gain": 0.0,
                "gain_gw1": round(
                    next(p for p in players if p["id"] == move["element_in"])["xpts"]
                    - next(p for p in squad if p["id"] == move["element_out"])["xpts"], 1),
                "hit": index >= max(0, len(first_week.get("transfers") or []) - first_hits),
                "optimizer": "horizon-milp-v4.1",
            })
        new_bank = int(first_week.get("bank_after") or bank)
        used_free = min(max(0, int(free_transfers)), len(transfers))
        ft_left = max(0, int(free_transfers) - used_free)
        notes = [
            f"V4.1 horizon MILP objective {horizon_plan['objective']:.2f}",
            f"next-GW FT state {first_week.get('free_transfers_after')}",
        ]
    if not paid_transfers_calibrated and free_transfers <= 0:
        notes.append(
            f"paid transfers disabled until {paid_transfer_min_gws} completed GWs calibrate V4"
        )
    print(f"transfers: {len(transfers)} | bank after {new_bank} | FTs left {ft_left} | {notes}")

    if not active_transfer_chip:
        final_squad = list(squad)
        for t in transfers:
            final_squad = [next(p for p in players if p["id"] == t["element_in"])
                           if p["id"] == t["element_out"] else p for p in final_squad]
        final_utility = squad_horizon_utility(
            final_squad, float(settings.get("v4_transfer_risk_penalty", 0.25)),
            float(settings.get("v4_bench_depth_weight", 0.08)))
        package_gain = round(final_utility - current_utility, 1)
        for transfer in transfers:
            transfer["gain"] = package_gain if len(transfers) == 1 else transfer["gain_gw1"]
            transfer["package_gain"] = package_gain

    # --- lineup + captain on final squad ---
    # Excluded players are given xPts 0 for the solver -> can never start and
    # can never be captain; their real xPts stay in the players pool for display.
    lineup_squad = [{**p, "xpts": 0.0} if p["id"] in exclude_ids else p for p in final_squad]
    if active_transfer_chip:
        # No MILP lineup for a rebuild — solve the XI directly on the new 15.
        try:
            starters, bench = solve_lineup(lineup_squad, line_max=rebuild_lineup_max)
        except Exception as error:
            print(f"!! {active_transfer_chip} lineup solve failed - no plan persisted: {error!r}")
            sys.exit(1)
    else:
        selected_lineup = set(first_week.get("lineup_ids") or [])
        selected_bench = list(first_week.get("bench_ids") or [])
        starters = [p for p in lineup_squad if p["id"] in selected_lineup]
        bench_by_id = {p["id"]: p for p in lineup_squad if p["id"] not in selected_lineup}
        bench = [bench_by_id[player_id] for player_id in selected_bench]
    if len(starters) != 11 or len(bench) != 4:
        print("!! incomplete lineup returned - no plan persisted")
        sys.exit(1)
    cap = (max(starters, key=lambda p: p["xpts"]) if active_transfer_chip
           else next(p for p in starters if p["id"] == first_week["captain_id"]))
    vice = select_vice(
        starters, cap,
        min_start=float(settings.get("v4_captain_min_start", 0.75)),
        min_minutes=float(settings.get("v4_captain_min_minutes", 65)),
    )
    captain_evidence = captain_rankings(
        starters, captain_id=cap["id"],
        min_start=float(settings.get("v4_captain_min_start", 0.75)),
        min_minutes=float(settings.get("v4_captain_min_minutes", 65)),
    )
    xi_total = sum(p["xpts"] for p in starters)
    print(f"XI xPts {xi_total:.1f} | captain {cap['name']} ({cap['xpts']:.1f}) | VC {vice['name']}")

    # Chip advice is advisory and manually staged. It may never mutate the
    # canonical MILP transfer path behind the owner's back.
    chip_suggestion = None
    used_chips = {}
    try:
        sys.path.insert(0, os.path.join(BASE, "model"))
        import chip_advisor
        try:
            sys.path.insert(0, os.path.join(BASE, "execution"))
            import chips
            used_chips = chips.fetch_used_chips(team_id)
            chip_windows = chips.fetch_chip_windows()
            if used_chips:
                print(f"chip advisor: used chips this season: {used_chips}")
        except Exception as e:
            print(f"chip advisor: used-chips check skipped: {repr(e)[:100]}")
            chip_windows = None
        advisor_plan = {
            "target_starters": starters,
            "bench": bench,
            "captain": cap,
        }
        chip_suggestion = chip_advisor.advise(advisor_plan, fixtures, gw, team_id,
                                              players=players, squad=squad, bank=bank,
                                              windows=chip_windows, used_chips=used_chips)
        if chip_suggestion:
            print(f"chip advisor: suggest {chip_suggestion['chip']} ({chip_suggestion['reason']})")
        else:
            print("chip advisor: no DGW/BGW opportunity - keep chips")
    except Exception as e:
        print(f"chip advisor skipped: {repr(e)[:120]}")
        chip_suggestion = None

    if (chip_suggestion and chip_suggestion.get("transfer_in")
            and chip_suggestion["transfer_in"].get("id") not in candidate_ids):
        print("chip advisor suggestion invalidated by the competitive template gate")
        chip_suggestion = None

    if chip_suggestion and chip_suggestion.get("transfer_in"):
        chip_suggestion["advisory_only"] = True
        chip_suggestion["execution_note"] = (
            "Chip-related transfer ideas are not merged into the executable plan; "
            "run a fresh simulation after explicitly staging the chip."
        )

    # --- validation of final squad (HARD GATE: no plan, no card if invalid) ---
    from collections import Counter
    quota = Counter(p["position"] for p in final_squad)
    clubs = Counter(p["club"] for p in final_squad)
    total_cost = sum(p["cost"] for p in final_squad)
    # P0.8: an appreciated squad's market cost can exceed £100m while being
    # perfectly legal - validate CASH FLOW (live selling value + bank), not
    # market cost vs the initial ceiling.
    total_sell = sum(p.get("selling_price", p["cost"]) for p in final_squad)
    cash_in = sum(int(t["purchase_price"]) for t in transfers)
    cash_out = sum(int(t["selling_price"]) for t in transfers)
    validation = {
        "size_ok": len(final_squad) == 15,
        "quota_ok": dict(quota) == SQUAD_QUOTA,
        "club_ok": max(clubs.values()) <= 3,
        "budget_ok": total_sell + bank >= total_cost,
        "cash_ok": cash_in - cash_out <= bank,
        "total_cost": total_cost / 10,
        "total_sell_value": total_sell / 10,
    }
    print(f"validation: {validation}")
    if not all([validation["size_ok"], validation["quota_ok"], validation["club_ok"],
                validation["budget_ok"], validation["cash_ok"]]):
        print("!! VALIDATION FAILED - plan discarded, no card sent. Squad unchanged.")
        # P0.9: non-zero RC so fpl_auto.py does NOT record plan_gw and retries.
        sys.exit(1)

    # --- persist plan + predictions (atomic, P0.10) ---
    plan = {
        "schema_version": 2,
        "run_id": run_id,
        "optimizer_version": "v4.1",
        "projection_version": projection_version,
        "team_id": team_id,
        "gw": gw,
        "generated_at": now.isoformat(),
        "deadline": next_gw["deadline_time"],
        "transfers": transfers,
        # Live account state used to produce this proposal.  These fields are
        # part of the decision contract so a transfer made outside Telegram
        # cannot leave an apparently valid approval card behind.
        "free_transfers_before": free_transfers,
        "free_transfers_after": ft_left,
        "bank_before": bank / 10,
        "bank_after": new_bank / 10,
        "pre_transfer_squad_ids": sorted(p["id"] for p in squad),
        "target_starters": starters,
        "bench": bench,
        "captain": cap,
        "vice": vice,
        "current_xpts": round(current_total, 1),
        "target_xpts": round(sum(p["xpts"] for p in final_squad), 1),
        # Joint V4 packages expose the gain from legal XI/captain/bench utility.
        # Older or chip-forced transfers retain the raw-squad fallback.
        "horizon_gain": round(
            float(transfers[0].get("package_gain")) if transfers and
            transfers[0].get("package_gain") is not None else
            sum(p["xpts_horizon"] for p in final_squad) - current_horizon,
            1,
        ),
        "validation": validation,
        "chip_suggestion": chip_suggestion,
        "data_note": "Official FPL + calibrated statistical V4; betting odds are not used.",
        "bonus_note": bonus_note,
        "prefs": {"keep": sorted(keep_ids), "exclude": sorted(exclude_ids)},
        "model_version": "competitive-v4.0",
        "chip": active_transfer_chip,
        "chip_gw": gw if active_transfer_chip else None,
        "chip_detected_from_live_fpl": bool(active_transfer_chip),
        "engine_note": (
            f"{active_transfer_chip.title()} detected in official FPL - full 15-player "
            f"rebuild - {projection_version} projection" if active_transfer_chip
            else f"{projection_version} projection + horizon MILP v4.1"
        ),
        "status": "pending",
        # Sol GW1 directive W1: proposal identity + provenance
        # (plan_id/input_fp computed below AFTER the dict is complete so the
        # approval-side canonical_plan_hash(plan) sees the identical shape)
        "plan_id": None,
        "input_fp": None,
        "engine_display": "competitive-v4.0 + optimizer-v4.1",
        "horizon_plan": horizon_plan,
        "captain_rankings": captain_evidence[:3],
    }
    # Adaptive league intelligence is allowed to refine captain variance only
    # inside explicit xPts guardrails. It never changes transfers, projections,
    # squad legality, or the approval gate. Missing/stale state fails soft.
    if li_cfg.get("enabled") and li_cfg.get("captain_refinement_enabled", True):
        try:
            from opponent_intelligence import refine_plan_captain
            plan = refine_plan_captain(
                plan,
                protect_guardrail=float(li_cfg.get("protect_xpts_guardrail", 0.5)),
                chase_guardrail=float(li_cfg.get("chase_xpts_guardrail", 1.0)),
            )
            cap = plan["captain"]
            vice = plan["vice"]
            li_audit = plan.get("league_intelligence", {})
            print(
                f"league intelligence: mode={li_audit.get('mode')} "
                f"captain_refined={bool(li_audit.get('applied'))}"
            )
        except Exception as e:
            plan["league_intelligence"] = {
                "applied": False,
                "mode": "Neutral",
                "reason": f"League intelligence failed soft: {repr(e)[:100]}",
            }
            print(f"league intelligence skipped: {repr(e)[:120]}")
    # Attach the same validated V4 context consumed by the dashboard.  The
    # packet is read-only; Telegram's existing live safety gates still decide
    # whether an approved plan may write to FPL.
    if competitive_context:
        plan["competitive"] = competitive_context
        plan["competitive"]["candidate_gate"] = template_candidate_gate
        plan["model_version"] = "competitive-v4.0"
        plan["engine_display"] = "competitive-v4.0"
        plan["engine_note"] = (
            f"competitive-v4.0 canonical packet • {projection_version} projection • optimizer-v4.1"
        )
        print(f"competitive V4: phase={competitive_context.get('phase')} alignment={competitive_context.get('alignment')} core={competitive_context.get('core_owned')}/{competitive_context.get('core_size')}")
    else:
        # Official projections can still produce a safe team sheet, but the
        # transfer pool remains locked without same-run league context.
        plan["model_version"] = "competitive-v4.0"
        plan["engine_display"] = "competitive-v4.0 + optimizer-v4.1"
        plan["engine_note"] = f"{projection_version} projection + horizon MILP v4.1"
        plan["competitive"] = {
            "model_version": "competitive-v4.0", "fallback": False,
            "context_status": "lineup_only_safe", "phase": None, "alignment": None,
            "target_alignment": 82, "fallback_reason": str(competitive_error),
            "template_gate": {"decision": "CONTEXT_PENDING", "differential_allowed": False,
                               "alignment_threshold": 82},
            "meta": {"stale": True, "freshness_hours": None},
        }
        print(f"competitive context unavailable; transfers locked, lineup-only safe mode ({competitive_error})")
    # Candidate evidence is display-only. It is deliberately excluded from the
    # canonical execution model_version and cannot make the packet executable.
    try:
        with open(os.path.join(BASE, "data", "processed", "v42_candidate_state.json"),
                  encoding="utf-8") as candidate_handle:
            candidate_state = json.load(candidate_handle)
        plan["model_candidate"] = {
            "version": candidate_state.get("candidate", "competitive-v4.2-shadow"),
            "status": candidate_state.get("owner_status", "awaiting_eligibility"),
            "evaluated_gws": candidate_state.get("evaluated_gws", []),
            "rows": candidate_state.get("n_rows", 0),
            "eligible_for_owner_approval": bool(
                candidate_state.get("eligible_for_owner_approval")),
            "checks": candidate_state.get("checks", {}),
        }
    except (OSError, ValueError, TypeError):
        plan["model_candidate"] = {
            "version": "competitive-v4.2-shadow", "status": "collecting_first_result",
            "evaluated_gws": [], "rows": 0, "eligible_for_owner_approval": False,
        }
    competitive_meta = (plan.get("competitive") or {}).get("meta") or {}
    source_manifest = {
        "status": "ready" if competitive_context and not refresh_failures else "lineup_only_safe",
        "run_id": run_id,
        "projection": {"status": "ready", "version": projection_version,
                       "registry": "owner-approved" if projection_version == "competitive-v4.2"
                       else "production-default"},
        "official_fpl": {"status": "ready", "fetched_at": now.isoformat(), "source": "official-fpl-api"},
        "account": {"status": "ready" if len(squad) == 15 and free_transfers_synced else "invalid",
                    "fetched_at": now.isoformat(), "squad_count": len(squad),
                    "free_transfers": free_transfers},
        "league": {"status": "ready" if competitive_context else "unavailable",
                   "run_id": competitive_meta.get("run_id"),
                   "snapshot_at": competitive_meta.get("snapshot_at"),
                   "freshness_hours": competitive_meta.get("freshness_hours")},
        "refresh_failures": refresh_failures,
    }
    live_picks = team.get("picks") or []
    live_starter_ids = {int(row["element"]) for row in live_picks
                        if int(row.get("position") or 99) <= 11}
    proposed_starter_ids = {int(player["id"]) for player in starters}
    names = {int(player["id"]): player["name"] for player in players}
    live_captain_id = next((int(row["element"]) for row in live_picks if row.get("is_captain")), None)
    live_vice_id = next((int(row["element"]) for row in live_picks if row.get("is_vice_captain")), None)
    team_diff = {
        "started": [names.get(player_id, str(player_id)) for player_id in sorted(proposed_starter_ids - live_starter_ids)],
        "benched": [names.get(player_id, str(player_id)) for player_id in sorted(live_starter_ids - proposed_starter_ids)],
        "captain_from": names.get(live_captain_id) if live_captain_id != cap["id"] else None,
        "captain_to": cap["name"] if live_captain_id != cap["id"] else None,
        "vice_from": names.get(live_vice_id) if live_vice_id != vice["id"] else None,
        "vice_to": vice["name"] if live_vice_id != vice["id"] else None,
    }
    team_diff["write_required"] = bool(
        transfers or team_diff["started"] or team_diff["benched"]
        or team_diff["captain_to"] or team_diff["vice_to"]
    )
    team_diff["approval_action"] = (
        "APPROVE TRANSFER + TEAM SHEET" if transfers else
        ("APPLY TEAM SHEET" if team_diff["write_required"] else "ACKNOWLEDGE")
    )
    plan["decision_summary"] = build_decision_summary(
        plan,
        squad=squad,
        final_squad=final_squad,
        candidates=candidates,
        starters=starters,
        gw_ids=gw_ids,
        bank=bank,
        free_transfers=free_transfers,
        paid_transfers_calibrated=paid_transfers_calibrated,
        paid_transfer_min_gws=paid_transfer_min_gws,
        calibration=decision_calibration,
        generated_at=now.isoformat(),
        deadline=next_gw["deadline_time"],
        free_transfers_synced=free_transfers_synced,
        solver_settings={
            "risk_penalty": float(settings.get("v4_transfer_risk_penalty", 0.25)),
            "bench_weight": float(settings.get("v4_bench_depth_weight", 0.08)),
            "approval_cutoff_minutes": int(settings.get("approval_cutoff_minutes", 30)),
        },
        horizon_plan=horizon_plan,
        captain_rankings=captain_evidence,
        source_manifest=source_manifest,
        team_diff=team_diff,
    )
    plan_errors = validate_plan(plan, now=now)
    if plan_errors:
        print("!! PLAN VALIDATION FAILED - no plan persisted and no card sent")
        for error in plan_errors:
            print(f"   - {error}")
        sys.exit(1)
    plan["validation"]["lineup_ok"] = True
    # canonical identity over the FULL plan (execution semantics only)
    plan["plan_id"] = canonical_plan_hash(plan)
    plan["decision_summary"]["plan_id"] = plan["plan_id"]
    source_fp = hashlib.sha256(json.dumps(
        source_manifest, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")).hexdigest()
    plan["input_fp"] = input_fingerprint(
        gw, f"competitive-v4.0/{projection_version}", next_gw["deadline_time"],
        odds_fp=None,
        settings_fp=settings_fingerprint(settings),
        run_id=run_id, source_fp=source_fp)

    # --- dedup: skip Telegram card if the PLAN is unchanged since last run ---
    plan_sig = {
        "transfers": [[t["element_out"], t["element_in"]] for t in transfers],
        "starters": [p["id"] for p in starters],
        "bench": [p["id"] for p in bench],
        "captain": cap["id"],
        "vice": vice["id"],
        "target_xpts": round(sum(p["xpts"] for p in final_squad), 1),
        "competitive": competitive_notification_signature(plan),
    }
    last_plan = None
    last_plan_path = os.path.join(BASE, "data", "processed", "pending_plan.json")
    if os.path.exists(last_plan_path):
        try:
            with open(last_plan_path) as f:
                last_plan = json.load(f)
        except Exception:
            last_plan = None
    if last_plan:
        last_sig = {
            "transfers": [[t["element_out"], t["element_in"]] for t in last_plan.get("transfers", [])],
            "starters": [p["id"] for p in last_plan.get("target_starters", [])],
            "bench": [p["id"] for p in last_plan.get("bench", [])],
            "captain": (last_plan.get("captain") or {}).get("id"),
            "vice": (last_plan.get("vice") or {}).get("id"),
            "target_xpts": last_plan.get("target_xpts"),
            "competitive": competitive_notification_signature(last_plan),
        }
    send_card = args.force_notify or not last_plan or plan_sig != last_sig
    if not send_card:
        print("plan unchanged since last run - Telegram card NOT sent (dedup)")
    elif args.force_notify:
        print("Telegram card notification forced by operator")

    atomic_write_json(last_plan_path, plan)
    # per-GW snapshot for the post-GW decision audit
    atomic_write_json(os.path.join(BASE, "data", "processed", f"plan_gw{gw}.json"), plan)

    preds = [{"id": p["id"], "name": p["name"], "pos": p["position"], "xpts": round(p["xpts"], 2),
              "floor": p.get("xpts_floor"), "upside": p.get("xpts_upside"),
              "p_start": p.get("p_start"), "expected_minutes": p.get("expected_minutes"),
              "p_dnp": p.get("p_dnp"), "p_1_59": p.get("p_1_59"),
              "p_60_plus": p.get("p_60_plus"),
              "xpts_by_gw": p.get("xpts_by_gw"), "variance_by_gw": p.get("variance_by_gw"),
              "status": p.get("status"), "cop": p.get("cop"), "news": p.get("news")} for p in players]
    atomic_write_json(os.path.join(BASE, "data", "processed", f"predictions_gw{gw}.json"),
                      {"gw": gw, "generated_at": now.isoformat(),
                       "model_version": "competitive-v4.0",
                       "projection_version": projection_version, "players": preds})

    # P0.9: verify the persisted plan before declaring success
    try:
        with open(last_plan_path, encoding="utf-8") as f:
            chk = json.load(f)
        if chk.get("status") not in ("pending", "fallback") or chk.get("gw") != gw:
            print("!! PERSIST VERIFY FAILED - plan file does not match this run")
            sys.exit(1)
    except Exception as e:
        print(f"!! PERSIST VERIFY FAILED - cannot re-read plan ({repr(e)[:100]})")
        sys.exit(1)

    # --- approval card (rich media tables) ---
    # Display-only artifact, outside the canonical approval payload/hash.
    # Optional publication failure must not change the existing Telegram flow.
    try:
        from dashboard_packet import export_plan
        export_plan(BASE, plan, team, players, bootstrap, fixtures)
    except Exception as error:
        print(f"private dashboard publication failed: {type(error).__name__}")

    sys.path.insert(0, os.path.join(BASE, "bot"))
    from templates import plan_card
    card = plan_card(plan)
    print("\n" + card.replace("<pre>", "```").replace("</pre>", "```").replace("<b>", "**").replace("</b>", "**") + "\n")

    chat_id = settings.get("telegram", {}).get("chat_id")
    token = creds.get("TELEGRAM_BOT_TOKEN")
    if send_card and chat_id and token and not args.notifications_disabled:
        res = send_telegram_card(card, chat_id, token)
        print("card sent to Telegram:", bool(res and res.get("ok")))

    print("saved pending_plan.json + predictions_gw%d.json + plan_gw%d.json" % (gw, gw))


if __name__ == "__main__":
    main()
