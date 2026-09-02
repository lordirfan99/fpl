"""
FPL Autopilot - post-GW decision audit (attribution).

Separates WHY the GW under/over-performed instead of just reporting points.
The spec (7 Aug audit) demanded these categories be separated so
"retraining" is not just accumulating residuals:

  1. bad prediction      - model predicted X, player scored Y (model error)
  2. bad minutes assumption - player predicted to play (high xPts) who barely
                           played (minutes < 45 with prediction >= 4)
  3. captain variance    - captain actual vs best-other-starter actual
  4. transfer decision   - element_in scored vs element_out scored (net of hit)
  5. injury after deadline - planned player now flagged injured/unavailable
  6. bench points        - points left on the bench
  7. chip outcome        - if a chip was played: projection vs actual
  8. luck vs process     - aggregate residual: XI actual vs XI predicted

Pure functions, unit-tested. Callers: jobs/post_gw_review.py (live),
tests (mocked data). Run standalone:
    .venv/Scripts/python.exe jobs/post_gw_audit.py <gw>
"""


def build_audit(plan, actuals, minutes, current_elements, gw_points=None, chip_played=None):
    """Return an audit dict with per-category findings.

    plan:   the plan_gw{gw}.json snapshot for the GW (starters, bench,
            transfers, captain, target_xpts, chip...).
    actuals:{player_id: FPL points} from event/{gw}/live.
    minutes:{player_id: minutes played} from the same live payload.
    current_elements: {player_id: element} from TODAY's bootstrap (post-GW,
            so post-deadline injuries are visible).
    gw_points: actual total points scored in the GW (entry history).
    chip_played: the chip actually used in the GW (from entry picks chips[]),
            defaults to plan.get("chip").

    Returns {"categories": {...}, "lines": [...], "summary": {...}}.
    """
    actuals = actuals or {}
    minutes = minutes or {}
    current_elements = current_elements or {}
    chip_played = chip_played or plan.get("chip")
    lines = []

    starters = plan.get("target_starters", [])
    bench = plan.get("bench", [])
    transfers = plan.get("transfers", [])
    cap = plan.get("captain") or {}
    cap_id = cap.get("id")

    def nm(pid):
        return current_elements.get(pid, {}).get("web_name", str(pid))

    # --- 1 + 2: prediction / minutes assumptions on the XI ---
    bad_pred = []       # predicted >= 5, actual <= 1
    bad_mins = []       # predicted >= 4, played < 45
    pred_total = act_total = 0.0
    for p in starters:
        pid = p["id"]
        pred = float(p.get("xpts", 0))
        act = float(actuals.get(pid, 0))
        mins = int(minutes.get(pid, 0) or 0)
        pred_total += pred
        act_total += act
        if pred >= 5.0 and act <= 1.0:
            bad_pred.append((nm(pid), pred, act, mins))
        if pred >= 4.0 and mins < 45:
            bad_mins.append((nm(pid), pred, act, mins))
    if bad_pred:
        lines.append("🔴 Bad predictions: " + ", ".join(
            f"{n} (pred {p:.1f} -> {a})" for n, p, a, _ in bad_pred))
    if bad_mins:
        lines.append("⏱️ Bad minutes assumptions: " + ", ".join(
            f"{n} (pred {p:.1f}, played {m}')" for n, p, _, m in bad_mins))

    # --- 3: captain ---
    cap_pts = None
    cap_line = None
    if cap_id and cap_id in actuals:
        cap_pts = float(actuals[cap_id])
        best_other = max((float(actuals[p["id"]]) for p in starters if p["id"] != cap_id), default=0.0)
        cap_diff = cap_pts - best_other
        if cap_diff < -2.0:
            cap_line = (f"👑 Captain {nm(cap_id)} scored {cap_pts:.0f} "
                        f"(armband {cap_pts*2:.0f}); best other starter {best_other:.0f} "
                        f"({best_other*2:.0f}) - captaincy cost {abs(cap_diff*2):.0f} pts (variance or bad call)")
        elif cap_diff >= 0:
            cap_line = f"👑 Captain {nm(cap_id)} scored {cap_pts:.0f} (armband {cap_pts*2:.0f}) - best starter pick ✅"
        else:
            cap_line = f"👑 Captain {nm(cap_id)} scored {cap_pts:.0f} (armband {cap_pts*2:.0f}) - ok"
        lines.append(cap_line)

    # --- 4: transfers ---
    transfer_lines = []
    for t in transfers:
        pid_in, pid_out = t.get("element_in"), t.get("element_out")
        a_in = float(actuals.get(pid_in, 0))
        a_out = float(actuals.get(pid_out, 0))
        hit = -4.0 if t.get("hit") or t.get("chip_required") and False else 0.0
        net = a_in - a_out + hit
        verdict = "✅" if net > 0 else ("🟡" if net == 0 else "❌")
        transfer_lines.append(f"{verdict} {t.get('out_name', nm(pid_out))} -> {t.get('in_name', nm(pid_in))}: "
                              f"{a_in:.0f} vs {a_out:.0f} (net {net:+.0f})")
    if transfer_lines:
        lines.append("🔄 Transfers: " + " | ".join(transfer_lines))

    # --- 5: injury after deadline ---
    involved = []
    for t in transfers:
        involved.append(t.get("element_in"))
    for p in starters + bench:
        involved.append(p.get("id"))
    injured_after = []
    for pid in involved:
        if not pid:
            continue
        el = current_elements.get(pid, {})
        if el.get("status") in ("i", "u"):
            cop = el.get("chance_of_playing_next_round")
            injured_after.append(f"{nm(pid)} ({el.get('status')}, cop={cop})")
    if injured_after:
        lines.append("🏥 Injured after deadline: " + ", ".join(injured_after))
    else:
        lines.append("🏥 No injuries after deadline in planned players ✅")

    # --- 6: bench points ---
    bench_pts = sum(float(actuals.get(p["id"], 0)) for p in bench)
    if bench:
        top_bench = max((p for p in bench), key=lambda p: float(actuals.get(p["id"], 0)))
        lines.append(f"🪑 Bench points left: {bench_pts:.0f} "
                     f"(best: {nm(top_bench['id'])} {float(actuals.get(top_bench['id'], 0)):.0f})")

    # --- 7: chip outcome ---
    if chip_played:
        proj = float(plan.get("target_xpts", 0) or 0)
        actual = float(gw_points) if gw_points is not None else act_total
        diff = actual - proj
        lines.append(f"🎩 Chip {chip_played}: projected {proj:.1f}, actual {actual:.1f} ({diff:+.1f})")

    # --- 8: luck vs process (aggregate residual) ---
    residual = act_total - pred_total
    mae = round(sum(abs(float(actuals.get(p["id"], 0)) - float(p.get("xpts", 0)))
                     for p in starters) / max(1, len(starters)), 2)
    if residual > 8:
        luck = "outperformed model (+variance/luck)"
    elif residual < -8:
        luck = "underperformed model (-variance/luck)"
    else:
        luck = "in line with model (process working)"
    summary = {
        "xi_predicted": round(pred_total, 1),
        "xi_actual": round(act_total, 1),
        "residual": round(residual, 1),
        "xi_mae": mae,
        "captain_points": cap_pts,
        "bench_points": round(bench_pts, 1),
        "verdict": luck,
    }
    lines.append(f"📈 Luck vs process: XI predicted {pred_total:.1f} vs actual {act_total:.1f} "
                 f"({residual:+.1f}, MAE {mae:.2f}) -> {luck}")

    return {"categories": {
        "bad_predictions": bad_pred,
        "bad_minutes": bad_mins,
        "captain_line": cap_line,
        "transfers": transfer_lines,
        "injured_after_deadline": injured_after,
        "bench_points": round(bench_pts, 1),
        "chip_outcome": chip_played,
    }, "lines": lines, "summary": summary}
