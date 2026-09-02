"""
FPL Autopilot - Telegram message formatting.

Primary bot surfaces use native Telegram HTML instead of wide ASCII tables.
The legacy mono_table() helper is retained for compatibility/tests and for any
rare diagnostic output where fixed-width columns are still useful.
"""

import html


def esc(value):
    """HTML-escape a value for Telegram parse_mode=HTML."""
    return html.escape(str(value), quote=False)


def _mobile_safe_plan(lines, limit=4096):
    """Keep Telegram HTML within its limit without losing approval semantics."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    tail_index = next(
        (index for index, line in enumerate(lines) if line.startswith("\n⏰ <b>Deadline")),
        max(0, len(lines) - 3),
    )
    tail = lines[tail_index:]
    notice = "\n<i>Additional context remains available in the dashboard.</i>"
    kept = []
    reserved = len("\n".join(tail)) + len(notice) + 2
    for line in lines[:tail_index]:
        candidate = "\n".join(kept + [line])
        if len(candidate) + reserved > limit:
            break
        kept.append(line)
    return "\n".join(kept + [notice] + tail)


def mono_table(headers, rows):
    """Legacy aligned monospace table helper."""
    headers = [esc(h) for h in headers]
    rows = [[esc(c) for c in r] for r in rows]
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    lines = [sep]
    lines.append("| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |")
    lines.append(sep)
    for r in rows:
        lines.append("| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(r)) + " |")
    lines.append(sep)
    return "\n".join(lines)


def pre(text):
    return f"<pre>{text}</pre>"


def _role_badge(role):
    return {"C": " 👑 <b>C</b>", "VC": " 🥈 <b>VC</b>"}.get(str(role or ""), "")


def _position_icon(pos):
    return {"GKP": "🧤", "DEF": "🛡️", "MID": "⚙️", "FWD": "⚽"}.get(pos, "•")


def _player_line(name, cost=None, xpts=None, role="", prefix=""):
    details = []
    if cost not in (None, ""):
        details.append(f"<code>{esc(cost)}</code>")
    if xpts not in (None, ""):
        details.append(f"<b>{esc(xpts)}</b> xPts")
    suffix = f"  •  {'  •  '.join(details)}" if details else ""
    return f"{prefix}<b>{esc(name)}</b>{_role_badge(role)}{suffix}"


def team_message(gw, rows):
    """Professional mobile-first squad card.

    rows: (pick_position, pos_short, name, cost, xpts, role)
    """
    parsed = []
    for row in rows:
        pick_pos, pos, name, cost, xpts, role = row
        try:
            xp_num = float(xpts)
        except (TypeError, ValueError):
            xp_num = 0.0
        parsed.append({
            "pick": int(pick_pos), "pos": str(pos), "name": str(name),
            "cost": str(cost), "xpts": str(xpts), "xp_num": xp_num, "role": str(role or ""),
        })

    starters = [p for p in parsed if p["pick"] <= 11 and p["role"] != "SUB"]
    bench = [p for p in parsed if p not in starters]
    xi_xpts = sum(p["xp_num"] for p in starters)
    cap = next((p for p in starters if p["role"] == "C"), None)
    projected = xi_xpts + (cap["xp_num"] if cap else 0.0)

    lines = [
        f"🛡️ <b>MY TEAM • GW{esc(gw)}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📈 XI xPts <b>{xi_xpts:.1f}</b>   •   With captain <b>{projected:.1f}</b>",
    ]
    if cap:
        lines.append(f"👑 Captain <b>{esc(cap['name'])}</b> • {cap['xpts']} xPts → <b>{cap['xp_num'] * 2:.1f}</b>")

    lines.extend(["", "<b>STARTING XI</b>"])
    groups = [("GKP", "GOALKEEPER"), ("DEF", "DEFENDERS"), ("MID", "MIDFIELDERS"), ("FWD", "FORWARDS")]
    for pos, label in groups:
        players = [p for p in starters if p["pos"] == pos]
        if not players:
            continue
        lines.append(f"\n{_position_icon(pos)} <b>{label}</b>")
        for p in players:
            lines.append(_player_line(p["name"], p["cost"], p["xpts"], p["role"], "• "))

    lines.extend(["", "━━━━━━━━━━━━━━━━━━", "🪑 <b>BENCH • autosub order</b>"])
    if bench:
        for i, p in enumerate(sorted(bench, key=lambda x: x["pick"]), 1):
            lines.append(_player_line(p["name"], p["cost"], p["xpts"], p["role"], f"{i}️⃣ "))
    else:
        lines.append("No bench data available.")

    return "\n".join(lines)


def status_message(lines, pending_note=None):
    """Compact control-panel style status card."""
    icons = {
        "Team value": "💰", "Bank": "🏦", "Free transfers": "🔄",
        "Players": "👥", "Model": "🧠", "Next deadline": "⏰",
    }
    output = ["📊 <b>FPL AUTOPILOT STATUS</b>", "━━━━━━━━━━━━━━━━━━"]
    for label, value in lines:
        icon = icons.get(str(label), "•")
        output.append(f"{icon} <b>{esc(label)}</b>\n   {esc(value)}")
    if pending_note:
        output.extend([
            "", "━━━━━━━━━━━━━━━━━━",
            f"🟠 <b>PENDING APPROVAL</b>\n   {esc(pending_note)}",
            "   Review the plan card before execution.",
        ])
    else:
        output.extend(["", "🟢 No plan currently awaiting approval."])
    return "\n".join(output)


def plan_card(plan):
    """Mobile-first weekly decision card without wide fixed-width tables."""
    lines = [
        f"🧠 <b>FPL AUTOPILOT • GW{esc(plan.get('gw'))}</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    mv = plan.get("model_version")
    if mv:
        note = plan.get("engine_note") or mv
        model_line = f"🧠 <b>Model</b>\n   {esc(note)}"
        lines.append(model_line)
    candidate = plan.get("model_candidate") or {}
    if candidate:
        evaluated = len(candidate.get("evaluated_gws") or [])
        status = candidate.get("status") or "collecting"
        eligibility = "owner approval available" if candidate.get(
            "eligible_for_owner_approval") else "production unchanged"
        lines.append(
            f"🧪 <b>V4.2 candidate</b>\n   {esc(status)} • {evaluated}/6 live GWs"
            f" • {esc(candidate.get('rows', 0))}/500 rows • {esc(eligibility)}"
        )
    if plan.get("run_id") or plan.get("plan_id"):
        lines.append(
            f"🧾 <b>Decision identity</b>\n   Run <code>{esc(str(plan.get('run_id') or '—')[-21:])}</code>"
            f" • Plan <code>{esc(str(plan.get('plan_id') or '—')[:8])}</code>"
            f" • Optimizer {esc(plan.get('optimizer_version') or '—')}"
        )

    competitive = plan.get("competitive") or {}
    if competitive:
        context_status = competitive.get("context_status")
        phase = competitive.get("phase")
        alignment = competitive.get("alignment")
        target_alignment = competitive.get("target_alignment")
        meta = competitive.get("meta") or {}
        freshness = meta.get("freshness_hours")
        freshness_text = f"{float(freshness):.1f}h old" if freshness is not None else "age unknown"
        if context_status != "ready" or competitive.get("fallback"):
            lines.append("🛟 <b>SAFE MODE</b>\n   League context unavailable • transfers LOCKED • official-FPL lineup review only")
        else:
            lines.append(f"🎯 <b>Competitive V4</b>\n   {esc(phase or '—')} phase • alignment {esc(alignment)}% / target {esc(target_alignment)}% • {freshness_text}")
        missing = competitive.get("critical_missing") or []
        edges = competitive.get("model_edges") or []
        if missing:
            lines.append("   Core gap: " + ", ".join(esc(p.get("name", "?")) for p in missing[:3]))
        elif edges:
            lines.append("   Model edge: " + ", ".join(esc(p.get("name", "?")) for p in edges[:3]))

    summary = plan.get("decision_summary") or {}
    if summary:
        action = summary.get("recommended_action", "REVIEW")
        lines.extend([
            f"\n✅ <b>RECOMMENDED ACTION: {esc(action)}</b>",
            f"   {esc(summary.get('reason', 'Review the canonical plan.'))}",
        ])
        team_diff = summary.get("team_diff") or {}
        if team_diff:
            changes = []
            if team_diff.get("started"):
                changes.append("Start " + ", ".join(team_diff["started"]))
            if team_diff.get("benched"):
                changes.append("Bench " + ", ".join(team_diff["benched"]))
            if team_diff.get("captain_to"):
                changes.append(f"Captain {team_diff.get('captain_from') or '—'} → {team_diff['captain_to']}")
            if team_diff.get("vice_to"):
                changes.append(f"Vice {team_diff.get('vice_from') or '—'} → {team_diff['vice_to']}")
            lines.append(f"   <b>Live-team change:</b> {esc(' • '.join(changes) if changes else 'None — no FPL write required')}")

    target = plan.get("target_xpts")
    current = plan.get("current_xpts")
    gain = plan.get("horizon_gain", 0)
    lines.extend([
        f"📈 <b>Projection</b>\n   15-player GW pool: target <b>{esc(target)}</b> xPts • current {esc(current)} • 3-GW gain {float(gain):+.1f}",
    ])
    horizon = summary.get("horizon") or {}
    horizon_rows = horizon.get("rows") or []
    if horizon_rows:
        lines.append("   Risk-adjusted XI + captain + bench utility: " + " • ".join(
            f"GW{row.get('gw')} {float(row.get('current', 0)):.1f}→{float(row.get('proposed', 0)):.1f} ({float(row.get('gain', 0)):+.1f})"
            for row in horizon_rows
        ))
    uncertainty = summary.get("uncertainty") or {}
    if uncertainty:
        calibration = uncertainty.get("calibration") or {}
        lines.append(
            f"   🎲 With captain {float(uncertainty.get('mean_with_captain', 0)):.1f} • "
            f"80% outcome range {float(uncertainty.get('outcome_low', 0)):.1f}–{float(uncertainty.get('outcome_high', 0)):.1f}"
            + (f" • calibrated n={int(calibration.get('n', 0))}" if calibration.get("n") else "")
        )
        if calibration.get("gw_range") or calibration.get("interval_coverage") is not None:
            coverage = calibration.get("interval_coverage")
            lines.append(
                f"   Calibration GWs {esc(calibration.get('gw_range') or '—')}"
                + (f" • interval coverage {float(coverage):.0%}" if coverage is not None else "")
            )
    lines.append("")

    trs = plan.get("transfers", [])
    lines.append("🔄 <b>Transfers</b>")
    gate = competitive.get("template_gate") or {}
    if gate:
        decision = str(gate.get("decision") or "HOLD_TEMPLATE").replace("_", " ")
        lines.append(
            f"🧭 <b>Strategy:</b> {esc(decision)} • "
            f"differential {'OPEN' if gate.get('differential_allowed') else 'LOCKED'}"
        )
    if trs:
        for t in trs:
            hit = " • <b>−4 hit</b>" if t.get("hit") else ""
            lines.append(
                f"• <b>{esc(t.get('out_name', '?'))}</b> → <b>{esc(t.get('in_name', '?'))}</b>"
                f"  •  gain <b>{float(t.get('gain', 0)):+.1f}</b>{hit}"
            )
    else:
        no_move_reason = summary.get("reason") or "No legal transfer cleared the configured threshold."
        lines.append(f"✅ <b>Transfers:</b> none — {esc(no_move_reason)}")

    roadmap = summary.get("roadmap") or []
    if roadmap:
        lines.append("\n🗺️ <b>THREE-GW ROADMAP</b>")
        for row in roadmap:
            route = row.get("route") or {}
            moves = route.get("moves") or []
            move_text = ", ".join(f"{move.get('out')} → {move.get('in')}" for move in moves)
            suffix = f" • {move_text}" if move_text else ""
            state = f" • FT {row.get('free_transfers_before')}→{row.get('free_transfers_after')} next • bank £{float(row.get('bank_after') or 0):.1f}m"
            marker = "✅" if row.get("status") == "recommended" else "🔎"
            lines.append(f"{marker} GW{row.get('gw')}: <b>{esc(row.get('action'))}</b>{esc(suffix + state)}")
        lines.append("   Future routes are conditional and recalculated from live data.")
    paid_option = ((summary.get("alternatives") or {}).get("best_paid_transfer") or {})
    paid_moves = paid_option.get("moves") or []
    if paid_moves:
        paid_text = ", ".join(f"{move.get('out')} → {move.get('in')}" for move in paid_moves)
        allowed = (summary.get("alternatives") or {}).get("paid_transfer_allowed")
        verdict = "eligible for live threshold review" if allowed else "REJECTED — paid moves locked"
        lines.append(
            f"💸 <b>Best paid alternative:</b> {esc(paid_text)} • net after −4 "
            f"{float(paid_option.get('net_after_hit', 0)):+.1f} • {esc(verdict)}"
        )

    starters = plan.get("target_starters", [])
    bench = plan.get("bench", [])
    cap_id = (plan.get("captain") or {}).get("id")
    vc_id = (plan.get("vice") or {}).get("id")
    pos_rank = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}

    ordered = sorted(starters, key=lambda x: (pos_rank.get(x.get("position"), 9), -float(x.get("xpts", 0))))
    xi_total = sum(float(p.get("xpts", 0)) for p in ordered)
    lines.extend(["", f"🛡️ <b>STARTING XI</b> • {xi_total:.1f} base xPts"])
    formation_info = summary.get("formation") or {}
    if formation_info:
        lines.append(
            f"   Shape <b>{esc(formation_info.get('selected'))}</b> • elite route {esc(formation_info.get('template') or '—')}"
        )
        if formation_info.get("explanation"):
            lines.append(f"   {esc(formation_info.get('explanation'))}")
    last_pos = None
    for p in ordered:
        pos = p.get("position", "?")
        if pos != last_pos:
            lines.append(f"\n{_position_icon(pos)} <b>{esc(pos)}</b>")
            last_pos = pos
        role = "C" if p.get("id") == cap_id else ("VC" if p.get("id") == vc_id else "")
        lines.append(_player_line(p.get("name", "?"), None, f"{float(p.get('xpts', 0)):.1f}", role, "• "))

    cap_p = next((p for p in starters if p.get("id") == cap_id), None)
    if cap_p and cap_p.get("p_start") is not None:
        lines.extend([
            "",
            f"👑 <b>Captain confidence • {esc(cap_p.get('name'))}</b>",
            f"   Start {float(cap_p.get('p_start', 0)):.0%} • Exp mins {float(cap_p.get('expected_minutes', 0)):.0f}",
            f"   Floor {float(cap_p.get('xpts_floor', 0)):.1f} • Upside {float(cap_p.get('xpts_upside', 0)):.1f}",
        ])
    rankings = summary.get("captain_rankings") or []
    if rankings:
        lines.append("   <b>Captain ranking:</b> " + " • ".join(
            f"{row.get('name')} {float(row.get('xpts') or 0):.1f} xPts/"
            f"{float(row.get('p_start') or 0):.0%} start"
            + (" ✅" if row.get("eligible") else " ⚠️")
            for row in rankings[:3]
        ))

    lines.extend(["", "🪑 <b>BENCH • autosub order</b>"])
    if bench:
        for i, p in enumerate(bench, 1):
            role = "VC" if p.get("id") == vc_id else ""
            lines.append(_player_line(p.get("name", "?"), None, f"{float(p.get('xpts', 0)):.1f}", role, f"{i}️⃣ "))
    else:
        lines.append("No bench players in plan payload.")

    lines.extend(["", "━━━━━━━━━━━━━━━━━━"])
    sug = plan.get("chip_suggestion")
    if sug:
        lines.append(f"💡 <b>CHIP ADVISOR</b>\n   {esc(sug.get('reason'))} — {esc(sug.get('detail'))}")
    else:
        lines.append("💡 <b>CHIP ADVISOR</b>\n   Hold chips — no DGW/BGW edge detected.")

    # Odds are not a live input anymore. Keep the provenance line useful and
    # avoid showing a betting-market warning as if it affected the decision.
    lines.append("\n📐 <b>Data:</b> Official FPL + statistical V4 • betting odds not used")
    bnote = plan.get("bonus_note")
    if bnote:
        lines.append(f"\n⭐ {esc(bnote)}")
    try:
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "model"))
        from plan_context import plan_context_lines as _ctx_lines
        ctx = _ctx_lines(plan)
        if ctx:
            lines.append("\n🧭 <b>DECISION INPUTS & CONTEXT</b>")
            lines.extend(f"   {c}" for c in ctx)
    except Exception:
        pass  # fail-soft: card renders without context lines
    lines.append(f"\n⏰ <b>Deadline</b>\n   {esc(plan.get('deadline'))}")
    health = summary.get("data_health") or {}
    if health:
        league_age = health.get("league_snapshot_age_hours")
        league_text = f"{float(league_age):.1f}h" if league_age is not None else "unknown"
        lines.append(
            "\n🩺 <b>DATA HEALTH</b>\n"
            f"   FPL/account synced {'✅' if health.get('account_squad_synced') and health.get('free_transfers_synced') else '⚠️'}"
            f" • FT {esc(health.get('free_transfers'))} • league age {esc(league_text)}"
            f" • deadline gate {esc(health.get('deadline_safety', 'unknown'))}"
        )
    source_manifest = summary.get("source_manifest") or {}
    if source_manifest:
        league = source_manifest.get("league") or {}
        lines.append(
            f"   Snapshot contract {esc(source_manifest.get('status', 'unknown'))}"
            f" • league run {esc(str(league.get('run_id') or '—')[-12:])}"
        )
    scope = summary.get("approval_scope")
    approval_action = ((summary.get("team_diff") or {}).get("approval_action") or "APPROVE")
    lines.append(f"\n<b>Decision required:</b> {esc(approval_action)} or Reject below."
                 + (f"\n{esc(scope)}" if scope else ""))
    return _mobile_safe_plan(lines)


def history_message(rows):
    """Readable recent-GW timeline instead of a table."""
    output = ["📈 <b>RECENT GAMEWEEKS</b>", "━━━━━━━━━━━━━━━━━━"]
    if not rows:
        output.append("No completed gameweeks yet.")
        return "\n".join(output)

    previous_rank = None
    for gw, points, total, rank in rows:
        try:
            rank_int = int(rank)
            rank_text = f"{rank_int:,}"
        except (TypeError, ValueError):
            rank_int = None
            rank_text = str(rank)

        movement = ""
        if previous_rank is not None and rank_int is not None:
            delta = previous_rank - rank_int
            movement = f" • {'🟢 ▲' if delta > 0 else ('🔴 ▼' if delta < 0 else '⚪ =')} {abs(delta):,}" if delta else " • ⚪ ="
        output.append(
            f"\n<b>GW{esc(gw)}</b>  •  <b>{esc(points)} pts</b>\n"
            f"   Total {esc(total)} • Rank {esc(rank_text)}{movement}"
        )
        if rank_int is not None:
            previous_rank = rank_int
    return "\n".join(output)


def review_message(metrics, team_row, captain_row):
    """Post-GW review formatted as compact sections."""
    lines = ["📋 <b>POST-GW REVIEW</b>", "━━━━━━━━━━━━━━━━━━"]
    for label, value in metrics:
        lines.append(f"• <b>{esc(label)}</b>: {esc(value)}")
    if team_row:
        team, pts, total, rank = team_row
        lines.extend(["", f"👥 <b>{esc(team)}</b> • {esc(pts)} pts • Total {esc(total)} • Rank {esc(rank)}"])
    if captain_row:
        captain, scored, armband = captain_row
        lines.append(f"👑 <b>{esc(captain)}</b> • {esc(scored)} scored • {esc(armband)} with armband")
    return "\n".join(lines)
