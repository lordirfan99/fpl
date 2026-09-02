"""Plan context enrichments — DISPLAY ONLY (Sol pro-research SHIP list).

Adds deterministic safety-context items to approval/captaincy cards,
without touching xPts rankings, the solver, or the approval gate:
  1. Effective ownership (EO) of a player vs the field (template risk)
  2. Team-news freshness (age of last news timestamp)
  3. FPL API penalty/set-piece hierarchy (small curated fallback)
  4. Prize-aware multi-league mode and bounded captain-refinement audit

All functions are pure reads with fail-soft behavior: if any input is
missing, they return None and the card simply omits the line. No new
dependencies. Bonus stays disabled. No scoring coefficient introduced.
"""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Curated set-piece/penalty takers for 2026/27 (context only, from Scout
# set-piece takers + pre-season observation). Player -> role text.
# NOTE: keep tiny; this is display context, not a scoring coefficient.
SET_PIECE_CONTEXT = {
    "B.Fernandes": "PK + set pieces (MUN)",
    "Haaland": "PK taker (MCI)",
    "Semenyo": "set-piece threat (MCI, pre-season standout)",
    "Szoboszlai": "free-kicks (4 FK goals in 2025/26)",
    "Calvert-Lewin": "PK option (LEE)",
    "Gabriel": "CB threat on corners (ARS)",
    "Tarkowski": "CB threat on set pieces (EVE)",
    "Guéhi": "CB threat on set pieces (MCI)",
    "Truffert": "overlap LWB (BOU)",
}


def _load_bootstrap():
    """Return elements/teams lookup or (None, None) on any failure."""
    try:
        with open(os.path.join(BASE, "data", "raw", "bootstrap-static.json"), encoding="utf-8") as f:
            boot = json.load(f)
        els = {e["id"]: e for e in boot.get("elements", [])}
        teams = {t["id"]: t.get("short_name", "?") for t in boot.get("teams", [])}
        return els, teams
    except Exception:
        return None, None


def effective_ownership(player_id, cap_mult=2.0):
    """Return (raw_own_pct, effective_own_pct) or None.

    EO = raw ownership + captain share (assume cap_mult of captain-owned
    players count double vs the field). If ownership data absent, None.
    """
    els, _ = _load_bootstrap()
    if not els or player_id not in els:
        return None
    raw = els[player_id].get("selected_by_percent")
    try:
        raw = float(raw)
    except (TypeError, ValueError):
        return None
    # Estimate captain share: managers who own + captain him (top-1k trend).
    # Conservative default: 60% of owners captain a premium captain candidate.
    cap_share = min(raw * 0.60, 100.0) if raw > 20 else 0.0
    eo = raw + cap_share * (cap_mult - 1.0)
    return raw, eo


def news_age_hours(player_id):
    """Return hours since last news update for a player, or None if no news.

    Uses news_added (ISO) when present; otherwise None. Missing = None so
    the card can show 'no current news' rather than a bogus age.
    """
    els, _ = _load_bootstrap()
    if not els or player_id not in els:
        return None
    news = (els[player_id].get("news") or "").strip()
    added = els[player_id].get("news_added")
    if not news:
        return None
    if not added:
        return -1  # has news text but no timestamp: treat as unknown-old
    try:
        from datetime import datetime, timezone
        ts = datetime.fromisoformat(added.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - ts).total_seconds() / 3600.0)
    except Exception:
        return -1


def set_piece_line(name, player_id=None):
    """Return API-declared set-piece hierarchy, with curated fallback."""
    if not name:
        return None
    els, _ = _load_bootstrap()
    player = (els or {}).get(player_id) if player_id is not None else None
    if player is None and els:
        player = next((row for row in els.values() if str(row.get("web_name", "")).lower() == str(name).lower()), None)
    if player:
        roles = []
        for label, key in (("PK", "penalties_order"), ("direct FK", "direct_freekicks_order"), ("corners", "corners_and_indirect_freekicks_order")):
            try:
                order = int(player.get(key))
                roles.append(f"{label} #{order}")
            except (TypeError, ValueError):
                continue
        if roles:
            return " • ".join(roles) + " (FPL API)"
    for k, v in SET_PIECE_CONTEXT.items():
        if k.lower() in name.lower() or name.lower() in k.lower():
            return v
    return None


def plan_context_lines(plan):
    """Build display-only context lines for the plan card.

    Returns list[str]; empty list when nothing to show. Fails soft.
    """
    lines = []
    cap = (plan.get("captain") or {}).get("id")
    cap_name = (plan.get("captain") or {}).get("name")
    if cap is not None:
        eo = effective_ownership(cap)
        if eo:
            raw, eff = eo
            lines.append(
                f"👑 <b>Captain EO:</b> {cap_name} owned {raw:.0f}% "
                f"(effective ~{eff:.0f}%)"
            )
        age = news_age_hours(cap)
        if age is not None and age >= 0:
            lines.append(f"📰 <b>News age:</b> {cap_name} news {age:.0f}h ago")
        sp = set_piece_line(cap_name, cap)
        if sp:
            lines.append(f"⚽ <b>Set pieces:</b> {sp}")

    # penalty/set-piece context for key starters (display only)
    starters = plan.get("target_starters", []) or []
    sp_notes = []
    for p in starters[:6]:
        sp = set_piece_line(p.get("name"), p.get("id"))
        if sp:
            sp_notes.append(f"{p.get('name')}: {sp}")
    if sp_notes:
        lines.append("⚽ <b>Set-piece notes:</b> " + "; ".join(sp_notes[:3]))

    if plan:
        lines.extend(template_decision_lines(plan))
        lines.extend(league_intelligence_lines(plan))

    return lines


def template_decision_lines(plan):
    """Explain the elite-template gate and approved differential policy."""
    competitive = (plan or {}).get("competitive") or {}
    gate = competitive.get("template_gate") or {}
    template = competitive.get("elite_template") or []
    comparison = ((plan.get("decision_summary") or {}).get("template_comparison") or {})
    if not gate and not template:
        return []
    lines = []
    formation = competitive.get("template_formation") or "unknown"
    alignment = gate.get("alignment", competitive.get("alignment"))
    threshold = gate.get("alignment_threshold", competitive.get("target_alignment"))
    decision = gate.get("decision") or ("CONVERGE_TO_TEMPLATE" if alignment is not None and threshold is not None and alignment < threshold else "HOLD_TEMPLATE")
    lines.append(f"🧩 <b>Elite template:</b> {formation} • alignment {alignment}% / target {threshold}%")
    lines.append(f"🧭 <b>Decision rule:</b> {decision.replace('_', ' ')}")
    candidate_gate = competitive.get("candidate_gate") or {}
    if candidate_gate.get("applied"):
        lines.append("🔐 <b>Transfer pool:</b> elite-template players only while differentials are locked")
    owned = {int(p.get("id")) for p in (plan.get("target_starters", []) + plan.get("bench", [])) if p.get("id") is not None}
    gaps = comparison.get("missing") or [p for p in template if p.get("element") is not None and int(p["element"]) not in owned]
    if gaps:
        labels = []
        for p in gaps[:5]:
            pct = p.get("elite_percentage", p.get("percentage", 0))
            affordability = p.get("cash_affordable_with_one_move")
            marker = " ✓ cash-fit" if affordability is True else (" • needs funding" if affordability is False else "")
            labels.append(f"{p.get('name', '?')} ({float(pct or 0):.0f}%{marker})")
        more = f" +{len(gaps) - 5} more" if len(gaps) > 5 else ""
        lines.append("📌 <b>Top template gaps:</b> " + ", ".join(labels) + more)
    weak = [p.get("name", "?") for p in comparison.get("outside", [])]
    if not weak:
        template_ids = {int(p.get("element")) for p in template if p.get("element") is not None}
        for p in (plan.get("target_starters", []) + plan.get("bench", [])):
            if p.get("id") is not None and int(p["id"]) not in template_ids:
                weak.append(str(p.get("name", "?")))
    if weak:
        more = f" +{len(weak) - 5} more" if len(weak) > 5 else ""
        lines.append("🧱 <b>Outside template:</b> " + ", ".join(weak[:5]) + more)
    consensus = competitive.get("captain_consensus") or []
    if consensus:
        top = consensus[0]
        lines.append(f"👑 <b>Elite captain:</b> {top.get('name', '?')} ({float(top.get('percentage', 0)):.0f}%)")
    moves = competitive.get("transfer_consensus") or []
    if moves:
        lines.append("🔄 <b>Elite transfer consensus:</b> " + ", ".join(
            f"{m.get('name', '?')} ({float(m.get('percentage', 0)):.0f}%)" for m in moves[:3]
        ))
    return lines


def league_intelligence_lines(plan, state=None):
    """Prize/threat context from the latest fail-soft intelligence snapshot."""
    try:
        from opponent_intelligence import load_latest_state
        state = state or load_latest_state()
    except Exception:
        state = state or None
    audit = (plan or {}).get("league_intelligence") or {}
    if not state and not audit:
        return []
    lines = []
    mode = audit.get("mode") or ((state or {}).get("mode") or {}).get("mode") or "Neutral"
    reason = audit.get("reason") or ((state or {}).get("mode") or {}).get("reason")
    lines.append(f"🎯 <b>League mode:</b> {mode}" + (f" — {reason}" if reason else ""))
    if audit.get("applied"):
        lines.append(
            f"🧠 <b>Captain refined:</b> {audit.get('from')} → {audit.get('to')} "
            f"(xPts cost {float(audit.get('xpts_cost', 0)):.2f}, threat captain share {float(audit.get('captain_share', 0)):.0f}%)"
        )
    mode_state = (state or {}).get("mode") or {}
    target = mode_state.get("target") or {}
    if target.get("rank") is not None:
        current = (target.get("current_prize") or {}).get("prize", "outside prize bands")
        next_band = target.get("next_target") or {}
        next_text = next_band.get("prize")
        gap = target.get("gap_to_next_target")
        line = f"🏆 <b>Prize target L{target.get('league_id')}:</b> rank {target.get('rank')} • {current}"
        if next_text:
            line += f" • next {next_text}"
            if gap is not None:
                line += f" ({float(gap):.0f} pts)"
        lines.append(line)
        probability = target.get("probability") or {}
        if probability.get("available"):
            lines.append(
                f"🎲 <b>Prize simulation:</b> expected rank {float(probability.get('expected_rank', 0)):.0f} • "
                f"top 10 {float(probability.get('p_top_10', 0)):.1f}%"
            )
    for prize in (state or {}).get("prize_status", []) or []:
        special = prize.get("active_special") or []
        if special:
            first = special[0]
            lines.append(
                f"⚡ <b>GW{(state or {}).get('event')} special L{prize.get('league_id')}:</b> "
                f"rank {first.get('rank_from')} • {first.get('prize')} (top {special[-1].get('rank_to')} paid)"
            )
    if state:
        registry = state.get("registry") or {}
        lines.append(
            f"🔒 <b>Opponent data:</b> {int(state.get('cohort_count', 0))} monitored • "
            f"{int(state.get('trusted_pick_count', 0))} trusted locked squads • "
            f"registry {registry.get('status', 'unknown')}"
        )
        actionable = []
        owned_ids = {int(p.get("id")) for p in ((plan or {}).get("target_starters", []) + (plan or {}).get("bench", [])) if p.get("id") is not None}
        for signal in (state.get("market_signals") or []):
            try:
                chance = signal.get("chance_next")
                risk = signal.get("status") not in (None, "a") or (chance is not None and float(chance) < 75)
                if risk or int(signal.get("element")) in owned_ids:
                    direction = (signal.get("projection") or {}).get("direction") if isinstance(signal.get("projection"), dict) else None
                    actionable.append(f"{signal.get('name', '?')} {direction or 'availability risk'}")
            except (TypeError, ValueError):
                continue
        if actionable:
            lines.append("⚠️ <b>Actionable risk:</b> " + ", ".join(actionable[:5]))
    return lines


def haaland_eo_line():
    """Return the EO line for the Haaland decision card, or None."""
    els, _ = _load_bootstrap()
    if not els:
        return None
    haaland_id = next((eid for eid, e in els.items() if e.get("web_name") == "Haaland"), None)
    if haaland_id is None:
        return None
    eo = effective_ownership(haaland_id)
    if not eo:
        return None
    raw, eff = eo
    return (f"🚨 Haaland ownership: <b>{raw:.0f}%</b> "
            f"(effective ~{eff:.0f}% with captain share)")
