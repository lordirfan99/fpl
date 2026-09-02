"""
FPL Autopilot - chip advisor (DGW/BGW detection + chip suggestions).

Runs INSIDE the pre-deadline pipeline, not as a separate button. When the
pipeline generates a plan and requests approval, the advisor analyses
fixtures + squad and includes a chip suggestion IN THE APPROVAL CARD:

  - detect DGW (teams with 2 fixtures in one GW) / BGW (0 fixtures)
  - Bench Boost: strong bench + DGW teams in squad -> bench points double value
  - Triple Captain: strong captain + DGW -> 2x points become 3x
  - Free Hit: many squad players blanking -> FH avoids fielding < 11
  - Wildcard: heavy injuries / value decay / fixture swing -> reset

The advisor only SUGGESTS. The human stages the chip via the card button;
execution still goes through /approve (see chips.py for actual wiring).

P0.5 (7 Aug audit): the Wildcard trigger was dead in the real path because
pipeline player dicts never carried status/cop. Now handled via the type-safe
_injured_or_doubtful() helper (string status 'i'/'u' or numeric cop < 50).

Transfer ideas attached to chip advice are advisory only. They are never
coupled into the executable plan; the owner stages the chip and regenerates a
fresh canonical plan before approval.

P0.7 (7 Aug audit): chip availability is checked per bootstrap allocation
window (first half / second half), not per chip name - a first-half wildcard
does not block the second-half wildcard.

Pure functions, unit-tested.
"""
from collections import Counter

# suggestion confidence / priority ordering
SUGGEST_PRIORITY = ["3xc", "bboost", "freehit", "wildcard"]

_INJ_STATUSES = ("i", "u")


def _injured_or_doubtful(p):
    """Type-safe injury/doubt test. status in ('i','u') OR cop < 50 -> True.

    P0.5: the old expression `(p.get("status") or p.get("cop") or 100) < 50`
    raised TypeError the moment a string status ('i') reached the pipeline,
    and the wildcard trigger never fired in the real path because status/cop
    were never propagated. String status is checked FIRST; only then cop.
    A NUMERIC status is treated as a cop value for back-compat with legacy
    plan/test schemas that stored cop in the status field.
    """
    status = p.get("status")
    if status in _INJ_STATUSES:
        return True
    cop = p.get("cop")
    if cop is None:
        cop = p.get("chance_of_playing_next_round")
    if cop is None and status is not None:
        cop = status
    if cop is None:
        return False
    try:
        return float(cop) < 50
    except (TypeError, ValueError):
        return False


def _chip_available(code, gw, used_chips, windows):
    """Pure version of chips.chip_used_in_window (avoids network in advisor).

    used_chips may be {code: event} or {code: [events]}. windows may be None
    (then any use of the code blocks it - conservative) or the allocation list.
    """
    if not used_chips:
        return True
    played = used_chips.get(code)
    if not played:
        return True
    if isinstance(played, (int, str)):
        played = [played]
    played = [int(x) if x is not None else None for x in played]
    if not windows:
        return False
    for start, stop in windows.get(code, []):
        if start <= gw <= stop:
            return not any(pe is not None and start <= pe <= stop for pe in played)
    return False


def detect_dgw(fixtures, gw):
    """Return set of team ids with 2+ fixtures in `gw` (double gameweek)."""
    cnt = Counter()
    for f in fixtures:
        if f.get("event") == gw:
            cnt[f["team_h"]] += 1
            cnt[f["team_a"]] += 1
    return {t for t, c in cnt.items() if c >= 2}


def detect_bgw(fixtures, gw, n_teams=20):
    """Return set of team ids with 0 fixtures in `gw` (blank gameweek)."""
    cnt = Counter()
    for f in fixtures:
        if f.get("event") == gw:
            cnt[f["team_h"]] += 1
            cnt[f["team_a"]] += 1
    return {t for t in range(1, n_teams + 1) if cnt[t] == 0}


def _bench_players(plan):
    """Bench = the 4 non-starter ids in the plan (target_starters is XI only)."""
    starter_ids = {p["id"] for p in plan.get("target_starters", [])}
    bench = []
    for p in plan.get("bench", []):
        if p["id"] not in starter_ids:
            bench.append(p)
    return bench


def _club_counts(squad):
    counts = {}
    for p in squad:
        counts[p.get("club") or p.get("team")] = counts.get(p.get("club") or p.get("team"), 0) + 1
    return counts


def _legal_transfer_out(target, squad, club_max=3):
    """Best squad player to sell to bring `target` in, or None.

    P0.6: enforces same position, club cap (can't add a 4th player from a
    club unless we sell someone from that club), and rough affordability via
    bank + selling price (bank passed separately in _affordable). Returns the
    OUT player dict; the pipeline couples the transfer into the plan.
    """
    if not target or not squad:
        return None
    pos = target.get("position")
    t_club = target.get("club") or target.get("team")
    counts = _club_counts(squad)
    cands = [p for p in squad if p.get("position") == pos]
    # if target's club already has 3, we MUST sell someone from that club
    if counts.get(t_club, 0) >= club_max:
        cands = [p for p in cands if (p.get("club") or p.get("team")) == t_club]
    if not cands:
        return None
    # sell the lowest-horizon player of that position (least value lost)
    return max(cands, key=lambda p: -float(p.get("xpts_horizon") or p.get("xpts") or 0))


def advise(plan, fixtures, gw, team_id, players=None, squad=None, bank=0,
           windows=None, used_chips=None):
    """Return a suggestion dict (or None):
        {"chip": "3xc", "reason": "...", "detail": "...",
         "transfer_in": {...}, "transfer_out": {...}}
    players: ALL selectable players (id, name, position, club, cost, xpts) -
             allows the advisor to recommend transfers IN for chip value.
    squad: current squad player dicts (selling_price available).
    bank: current bank (in tenths, FPL units).
    used_chips: {api_code: [played_events]} from chips.fetch_used_chips.
    windows: {api_code: [(start, stop), ...]} from chips.fetch_chip_windows -
             used to respect second-half chip allocations (P0.7).
    """
    out = []

    used_chips = used_chips or {}
    dgw_teams = detect_dgw(fixtures, gw)
    bgw_teams = detect_bgw(fixtures, gw)

    def _chip_free(code):
        return _chip_available(code, gw, used_chips, windows)

    # NOTE: plan players carry the team as `club` (numeric team id), not `team`.
    def _club(p):
        return p.get("club") or p.get("team")

    squad_ids = set()
    for p in list(plan.get("target_starters", [])) + list(plan.get("bench", [])):
        if p.get("id"):
            squad_ids.add(p["id"])
    squad = squad or []

    def _best_dgw_player():
        """Highest-xPts selectable player whose club has a DGW. Market-wide."""
        if not players:
            return None
        cands = [p for p in players if p.get("club") in dgw_teams]
        if not cands:
            return None
        return max(cands, key=lambda p: p.get("xpts", 0) or 0)

    def _affordable(target, out_player):
        """Can we bring target in given bank + the selling price of out_player?"""
        if not target:
            return True
        sell = (out_player or {}).get("selling_price") or 0
        return bank + sell >= (target.get("cost") or 0)

    def _make_transfer_suggestion(chip, reason, detail, target):
        """Attach transfer_in/transfer_out only when a legal swap exists."""
        out_p = _legal_transfer_out(target, squad)
        if not out_p:
            return None  # not actionable -> never suggest
        if not _affordable(target, out_p):
            return None
        return {"chip": chip, "reason": reason, "detail": detail,
                "transfer_in": target, "transfer_out": out_p}

    # Triple Captain: captain xPts >= 7 AND his team has a DGW
    cap = plan.get("captain") or {}
    cap_ok = (cap.get("id") and (cap.get("xpts") or 0) >= 7.0
              and _club(cap) in dgw_teams)
    if cap_ok and _chip_free("3xc"):
        out.append({"chip": "3xc", "reason": "Triple Captain",
                    "detail": f"{cap.get('name','C')} ({cap.get('xpts'):.1f} xPts) has a DGW in GW{gw} - 3x points instead of 2x."})
    elif _chip_free("3xc"):
        # market-wide TC candidate: best DGW player NOT already in squad.
        # Only suggest if a legal transfer exists - an unactionable TC is noise.
        best = _best_dgw_player()
        if best and best["id"] not in squad_ids:
            sug = _make_transfer_suggestion(
                "3xc", "Triple Captain",
                (f"Transfer IN {best['name']} ({best.get('xpts', 0):.1f} xPts, "
                 f"£{best.get('cost', 0)/10:.1f}m) - best DGW player in GW{gw}, then TC."),
                best)
            if sug:
                out.append(sug)

    # Bench Boost: 3+ bench players >= 4 xPts AND any bench player's team has DGW
    bench = _bench_players(plan)
    bench_ok = [p for p in bench if (p.get("xpts") or 0) >= 4.0]
    bench_dgw = [p for p in bench_ok if _club(p) in dgw_teams]
    if len(bench_ok) >= 3 and bench_dgw and _chip_free("bboost"):
        out.append({"chip": "bboost", "reason": "Bench Boost",
                    "detail": f"{len(bench_ok)} bench players >=4 xPts, {len(bench_dgw)} with DGW in GW{gw} - bench points count."})
    elif bench and not bench_dgw and players and _chip_free("bboost"):
        # bench weak -> suggest bringing in a cheap DGW bench option
        cheap = [p for p in players if p.get("club") in dgw_teams and (p.get("cost") or 0) <= 55
                 and p["id"] not in squad_ids]
        if cheap:
            best_cheap = max(cheap, key=lambda p: p.get("xpts", 0) or 0)
            sug = _make_transfer_suggestion(
                "bboost", "Bench Boost",
                (f"Bench is weak for BB - transfer IN {best_cheap['name']} "
                 f"(£{best_cheap.get('cost', 0)/10:.1f}m, DGW GW{gw}) to boost bench."),
                best_cheap)
            if sug:
                out.append(sug)

    # Free Hit: 4+ squad players blanking (BGW) -> would field < 11 effectively
    all_squad = list(plan.get("target_starters", [])) + list(plan.get("bench", []))
    blanking = [p for p in all_squad if _club(p) in bgw_teams]
    if len(blanking) >= 4 and _chip_free("freehit"):
        out.append({"chip": "freehit", "reason": "Free Hit",
                    "detail": f"{len(blanking)} squad players blank in GW{gw} - FH avoids fielding fewer than 11."})

    # Wildcard: 4+ squad players injured/doubtful (P0.5: type-safe status/cop)
    injury_risk = [p for p in all_squad if _injured_or_doubtful(p)]
    if len(injury_risk) >= 4 and _chip_free("wildcard"):
        out.append({"chip": "wildcard", "reason": "Wildcard",
                    "detail": f"{len(injury_risk)} squad players injured/doubtful - WC resets the squad without hits."})

    if not out:
        return None

    # pick highest-priority suggestion (order in SUGGEST_PRIORITY)
    out.sort(key=lambda s: SUGGEST_PRIORITY.index(s["chip"]) if s["chip"] in SUGGEST_PRIORITY else 99)
    return out[0]
