"""Post-GW observations: distinguish frozen forecasts from official execution."""


def captain_context(plan, official_picks=None, chip_played=None):
    """Use the effective official captain, including vice fallback, when known."""
    if official_picks is not None:
        rows = official_picks.get('picks') or []
        effective = next((p for p in rows if int(p.get('multiplier') or 0) > 1), None)
        nominated = next((p for p in rows if p.get('is_captain')), None)
        chosen = effective or nominated
        return {'id': chosen.get('element') if chosen else None,
                'multiplier': int(chosen.get('multiplier') or 0) if chosen else 0,
                'chip': official_picks.get('active_chip'), 'source': 'official_entry_picks'}
    chip = chip_played if chip_played is not None else plan.get('chip')
    return {'id': (plan.get('captain') or {}).get('id'),
            'multiplier': 3 if chip == '3xc' else 2, 'chip': chip,
            'source': 'planned_only_unverified'}


def build_audit(plan, actuals, minutes, current_elements, gw_points=None,
                chip_played=None, official_picks=None):
    actuals, minutes, current_elements = actuals or {}, minutes or {}, current_elements or {}
    lines = []
    starters, bench, transfers = plan.get('target_starters', []), plan.get('bench', []), plan.get('transfers', [])
    cap = captain_context(plan, official_picks, chip_played)
    cap_id, multiplier, chip = cap['id'], cap['multiplier'], cap['chip']

    def nm(pid):
        return current_elements.get(pid, {}).get('web_name', str(pid))

    bad_pred, bad_mins = [], []
    pred_total = sum(float(p.get('xpts', 0)) for p in starters)
    complete = bool(starters) and all(p['id'] in actuals and p['id'] in minutes for p in starters)
    act_total = sum(float(actuals[p['id']]) for p in starters) if complete else None
    for p in starters:
        pid = p['id']
        if pid not in actuals or pid not in minutes:
            continue
        pred, act, mins = float(p.get('xpts', 0)), float(actuals[pid]), int(minutes[pid])
        if pred >= 5 and act <= 1:
            bad_pred.append((nm(pid), pred, act, mins))
        if pred >= 4 and mins < 45:
            bad_mins.append((nm(pid), pred, act, mins))
    if bad_pred:
        lines.append('🔴 Large forecast misses: '+', '.join(f'{n} (pred {p:.1f} -> {a:g})' for n,p,a,_ in bad_pred))
    if bad_mins:
        lines.append('⏱️ Minutes shortfalls: '+', '.join(f"{n} (pred {p:.1f}, played {m}')" for n,p,_,m in bad_mins))

    cap_pts, cap_line, cap_gap = None, None, None
    if cap_id in actuals:
        cap_pts = float(actuals[cap_id])
        label = 'official' if official_picks is not None else 'planned multiplier; unverified'
        cap_line = f'👑 Captain {nm(cap_id)}: {cap_pts:g} × {multiplier} = {cap_pts*multiplier:g} pts ({label}); extra over single-count XI base {cap_pts*max(0,multiplier-1):g}'
        if official_picks is not None:
            eligible = [p['element'] for p in official_picks.get('picks', [])
                        if int(p.get('position') or 99) <= 11 and p['element'] != cap_id
                        and minutes.get(p['element'], 0) > 0 and p['element'] in actuals]
        else:
            eligible = [p['id'] for p in starters if p['id'] != cap_id and minutes.get(p['id'],0)>0 and p['id'] in actuals]
        if eligible and multiplier > 1:
            best_other = max(float(actuals[pid]) for pid in eligible)
            cap_gap = (cap_pts-best_other)*(multiplier-1)
            cap_line += f'; extra-captain gap vs best other starting player {cap_gap:+g} pts (hindsight only)'
        lines.append(cap_line)

    transfer_lines = []
    for t in transfers:
        incoming, outgoing = t.get('element_in'), t.get('element_out')
        if incoming not in actuals or outgoing not in actuals:
            transfer_lines.append('Outcome unavailable for one transfer; not scored')
            continue
        a_in, a_out = float(actuals[incoming]), float(actuals[outgoing])
        net = a_in-a_out-(4 if t.get('hit') else 0)
        transfer_lines.append(f"{t.get('out_name',nm(outgoing))} -> {t.get('in_name',nm(incoming))}: {a_in:g} vs {a_out:g} (net {net:+g}, single GW only)")
    if transfer_lines:
        lines.append('🔄 Planned transfers: '+' | '.join(transfer_lines))

    flagged = []
    for pid in {p['id'] for p in starters+bench}:
        el = current_elements.get(pid, {})
        if el.get('status') in ('i','u','s'):
            flagged.append(f"{nm(pid)} ({el.get('status')})")
    lines.append('🏥 Current availability flags: '+(', '.join(flagged) if flagged else 'none among planned players')+'; injury onset relative to deadline is not verified')
    bench_pts = sum(float(actuals[p['id']]) for p in bench) if all(p['id'] in actuals for p in bench) else None
    if bench_pts is not None and bench:
        top = max(bench,key=lambda p:actuals[p['id']])
        lines.append(f"🪑 Planned bench raw points: {bench_pts:g} (best: {nm(top['id'])} {actuals[top['id']]:g}); not a recoverable-gain estimate")

    weighted = None
    if official_picks is not None:
        picks = official_picks.get('picks') or []
        if picks and all(p['element'] in actuals for p in picks):
            weighted = sum(float(actuals[p['element']])*int(p.get('multiplier') or 0) for p in picks)
            lines.append(f'📋 Official weighted-picks total: {weighted:g} (captain/chip/autosubs included)'
                         +(f'; entry history {gw_points:g}' if gw_points is not None else ''))
        lines.append(f"🎩 Official chip: {chip or 'none'}; frozen plan chip: {plan.get('chip') or 'none'}"
                     +(' — mismatch; activation path requires execution evidence' if chip != plan.get('chip') else ''))
    elif chip:
        lines.append(f'🎩 Planned chip: {chip}; official activation not verified')
    if chip == '3xc' and cap_pts is not None and official_picks is not None:
        marginal = cap_pts*max(0,multiplier-2)
        lines.append(f'🎩 Triple Captain incremental points vs normal captain: {marginal:g}; total captain contribution {cap_pts*multiplier:g}')

    residual = act_total-pred_total if act_total is not None else None
    mae = round(sum(abs(float(actuals[p['id']])-float(p.get('xpts',0))) for p in starters)/len(starters),2) if complete and starters else None
    verdict = 'outcomes incomplete; attribution unavailable'
    if residual is not None:
        verdict = ('below forecast' if residual < 0 else 'above forecast' if residual > 0 else 'equal to forecast')+'; cause undetermined (forecast error and/or match variance)'
        lines.append(f'📈 Forecast residual: planned XI predicted {pred_total:.1f} vs raw actual {act_total:.1f} ({residual:+.1f}, MAE {mae}) — excludes captain/chip extras and autosubs; {verdict}')
    else:
        lines.append('📈 Forecast residual unavailable: some official player outcomes are missing')
    summary = {'xi_predicted':round(pred_total,1),'xi_actual':round(act_total,1) if act_total is not None else None,
               'residual':round(residual,1) if residual is not None else None,'xi_mae':mae,'captain_points':cap_pts,
               'captain_multiplier':multiplier,'captain_total_points':cap_pts*multiplier if cap_pts is not None else None,
               'captain_extra_points':cap_pts*max(0,multiplier-1) if cap_pts is not None else None,
               'captain_hindsight_gap':cap_gap,'official_weighted_total':weighted,'entry_points':gw_points,
               'actual_chip':chip if official_picks is not None else None,'planned_chip':plan.get('chip'),
               'injury_timing_verified':False,'bench_points':bench_pts,'verdict':verdict}
    return {'categories':{'bad_predictions':bad_pred,'bad_minutes':bad_mins,'captain_line':cap_line,
                           'transfers':transfer_lines,'injured_after_deadline':flagged,'bench_points':bench_pts,'chip_outcome':chip},
            'lines':lines,'summary':summary}
