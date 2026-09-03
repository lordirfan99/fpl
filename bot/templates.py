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


def _short(name, n=11):
    name = str(name or "?")
    return name if len(name) <= n else name[: n - 1] + "…"


def _pre(rows):
    """Fixed-width block: rows are lists of (text, width, align) cells."""
    out = []
    for row in rows:
        cells = []
        for text, width, align in row:
            text = esc(text)
            cells.append(text.rjust(width) if align == "r" else text.ljust(width))
        out.append("".join(cells).rstrip())
    return "<pre>" + "\n".join(out) + "</pre>"


def plan_card(plan):
    """Compact Telegram decision card. Full detail lives in the dashboard."""
    plan = plan or {}
    gw = plan.get("gw")
    chip = str(plan.get("chip") or "").lower()
    trs = plan.get("transfers") or []
    summary = plan.get("decision_summary") or {}
    competitive = plan.get("competitive") or {}
    starters = plan.get("target_starters") or []
    bench = plan.get("bench") or []
    cap_id = (plan.get("captain") or {}).get("id")
    vc_id = (plan.get("vice") or {}).get("id")

    # --- headline action (chip-aware; a live rebuild is never "lineup only") ---
    if chip in ("wildcard", "freehit"):
        label = "WILDCARD" if chip == "wildcard" else "FREE HIT"
        action = f"{label} · full 15-player rebuild"
    else:
        n = len(trs)
        action = (summary.get("recommended_action")
                  or (f"{n} TRANSFER{'S' if n != 1 else ''} + team sheet" if trs
                      else "TEAM SHEET ONLY"))
    safe = (not chip) and (competitive.get("context_status") not in (None, "ready")
                           or competitive.get("fallback"))

    lines = [
        f"\U0001f9e0 <b>FPL AUTOPILOT · GW{esc(gw)}</b>",
        f"✅ <b>RECOMMENDED ACTION: {esc(action)}</b>"
        + ("  ⚠️ transfers held (safe mode)" if safe and trs else ""),
    ]
    reason = summary.get("reason")
    if reason and not chip:
        # A chip rebuild is never "transfers locked"; suppress the safe-mode reason.
        lines.append(f"   {esc(reason)}")
    gate = competitive.get("template_gate") or {}
    owned_ids = {p.get("id") for p in starters + bench}
    template = competitive.get("elite_template") or []
    if gate:
        decision = str(gate.get("decision") or "HOLD_TEMPLATE").replace("_", " ")
        lines.append(f"\U0001f9ed <b>Strategy:</b> {esc(decision)} · differential "
                     f"{'OPEN' if gate.get('differential_allowed') else 'LOCKED'}")
        # The "why": the elite-template players the plan still doesn't own.
        incoming = {t.get("element_in") for t in trs}
        gaps = sorted(
            (t for t in template
             if t.get("element") not in owned_ids and t.get("element") not in incoming),
            key=lambda t: -float(t.get("elite_percentage") or 0),
        )
        if str(gate.get("decision")) == "CONVERGE_TO_TEMPLATE" and gaps:
            top = ", ".join(f"{esc(t.get('name'))} {float(t.get('elite_percentage') or 0):.0f}%"
                            for t in gaps[:3])
            lines.append(f"\U0001f3af <b>Template gap:</b> {top}")
    # V4.2 shadow model — only surface it when it's actually actionable.
    cand = plan.get("model_candidate") or {}
    if cand.get("eligible_for_owner_approval"):
        lines.append(f"\U0001f9ea <b>V4.2 candidate ready for approval</b> "
                     f"({len(cand.get('evaluated_gws') or [])}/6 GWs) — /promote to review")

    # --- key numbers ---
    tgt, cur = plan.get("target_xpts"), plan.get("current_xpts")
    gain = plan.get("horizon_gain")
    num = f"xPts {esc(tgt)}"
    if cur is not None:
        num += f" (now {esc(cur)}"
        if gain is not None:
            num += f", 3-GW {float(gain):+.1f}"
        num += ")"
    phase = competitive.get("phase")
    if phase and competitive.get("alignment") is not None:
        num += (f"  ·  {esc(phase)} align {esc(competitive.get('alignment'))}%"
                f"/{esc(competitive.get('target_alignment'))}%")
    lines.append(num)

    # --- transfers table ---
    if trs:
        template_ids = {t.get("element") for t in template}
        rows = []
        for t in trs:
            g = f"{float(t.get('gain', 0)):+.1f}"
            tail = f"{g} -4" if t.get("hit") else g
            if t.get("element_in") in template_ids:
                tail += " ✓"
            rows.append([
                (_short(t.get("out_name")), 12, "l"),
                ("→ ", 2, "l"),
                (_short(t.get("in_name")), 12, "l"),
                (tail, 9, "r"),
            ])
        lines.append("\U0001f504 <b>Transfers</b>  <i>(✓ = elite template)</i>")
        lines.append(_pre(rows))

    # --- starting XI table ---
    pos_rank = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    xi = sorted(starters, key=lambda p: (pos_rank.get(p.get("position"), 9),
                                         -float(p.get("xpts", 0))))
    rows = []
    for p in xi:
        mark = " (C)" if p.get("id") == cap_id else (" (V)" if p.get("id") == vc_id else "")
        rows.append([
            (str(p.get("position", "?")), 4, "l"),
            (_short(p.get("name"), 14) + mark, 18, "l"),
            (f"{float(p.get('xpts', 0)):.1f}", 5, "r"),
        ])
    xi_total = sum(float(p.get("xpts", 0)) for p in xi)
    shape = (summary.get("formation") or {}).get("selected")
    lines.append(f"\U0001f6e1️ <b>Starting XI</b> — {xi_total:.1f} xPts"
                 + (f" · {esc(shape)}" if shape else ""))
    lines.append(_pre(rows))

    # --- bench (one compact line) ---
    if bench:
        b = " · ".join(f"{i}. {_short(p.get('name'), 12)} {float(p.get('xpts', 0)):.1f}"
                            for i, p in enumerate(bench, 1))
        lines.append(f"\U0001fa91 <b>Bench</b>  {b}")

    # --- captain confidence (one line) ---
    cap_p = next((p for p in starters if p.get("id") == cap_id), None)
    if cap_p:
        bits = [f"\U0001f451 <b>{esc(cap_p.get('name'))}</b> (C)"]
        if cap_p.get("p_start") is not None:
            bits.append(f"{float(cap_p.get('p_start', 0)):.0%} start")
        if cap_p.get("xpts_floor") is not None:
            bits.append(f"floor {float(cap_p.get('xpts_floor', 0)):.1f}"
                        f"–{float(cap_p.get('xpts_upside', 0)):.1f}")
        lines.append("  ·  ".join(bits))

    # --- chip advisor (only if it has something to say) ---
    sug = plan.get("chip_suggestion")
    if sug and sug.get("chip"):
        lines.append(f"\U0001f4a1 Chip: {esc(sug.get('chip'))} — {esc(sug.get('reason'))}")

    # --- deadline + one-line health + approve prompt ---
    lines.append(f"\n⏰ <b>Deadline</b> {esc(plan.get('deadline'))}")
    health = summary.get("data_health") or {}
    if health:
        synced = health.get("account_squad_synced") and health.get("free_transfers_synced")
        lines.append(f"\U0001fa7a account {'✅' if synced else '⚠️'}"
                     f" · FT {esc(health.get('free_transfers'))}"
                     f" · <a href=\"https://fpl-scout-intelligence.netlify.app\">full detail → dashboard</a>")
    else:
        lines.append("<a href=\"https://fpl-scout-intelligence.netlify.app\">full detail → dashboard</a>")
    approval_action = ((summary.get("team_diff") or {}).get("approval_action")
                       or ("EXECUTE REBUILD" if chip else ("APPROVE" if trs else "APPROVE XI")))
    scope = summary.get("approval_scope")
    lines.append(f"\n<b>Decision required:</b> {esc(approval_action)} or Reject below · "
                 f"<code>{esc(str(plan.get('plan_id') or '')[:8])}</code>")
    if scope:
        lines.append(esc(scope))

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
