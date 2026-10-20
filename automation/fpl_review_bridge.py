#!/usr/bin/env python3
"""Validate web-only cron responses, publish advisory text, score finished GWs."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).parent))
import fpl_hybrid_review as core  # noqa: E402


def response_json(text):
    marker='\n## Response\n'
    if marker not in text:raise core.SidecarError('Cron output has no completed response section')
    response=text.rsplit(marker,1)[1].strip()
    if response=='[SILENT]':return None
    if response.startswith('```json\n') and response.endswith('\n```'):response=response[8:-4]
    value=json.loads(response)
    if not isinstance(value,dict):raise core.SidecarError('Research response must be one JSON object')
    return value


def brief(record):
    r=record['review'];s=record['snapshot'];names={e['id']:e['web_name'] for e in s['elements']}
    text=[f"FPL RESEARCH · GW{s['gameweek']} · plan {s['plan_id'][:8]}",
          f"{r['verdict'].upper()} · {r['suggested_action'].replace('_',' ')}",r['summary']]
    for p in r['affected_players'][:4]:text.append(f"• {names.get(p['id'],p['id'])}: {p['assumption']}")
    if record['hybrid_adjusted']:
        cap=next(p['element'] for p in record['hybrid']['picks'] if p.get('is_captain'))
        vice=next(p['element'] for p in record['hybrid']['picks'] if p.get('is_vice_captain'))
        text.append(f"Paper alternative only: captain {names[cap]}, vice {names[vice]}.")
    text.append('Sources: '+' | '.join(e['url'] for e in r['evidence'][:3]))
    if not record['prospective_eligible']:text.append('Late advisory: excluded from prospective comparison.')
    text.append('ADVISORY ONLY. No team change or approval was sent. Use the current FPL bot menu for any action.')
    return '\n'.join(text)


def sweep(state,output_dir,evaluate=True):
    state=Path(state);ledger_path=state/'bridge-ledger.json'
    with core.locked(state):ledger=core.read_json(ledger_path) if ledger_path.exists() else {'processed':{},'evaluated_day':None}
    messages=[]
    for path in sorted(Path(output_dir).glob('*.md')):
        if path.stat().st_size>4_000_000:raise core.SidecarError('Oversized cron response artifact')
        text=path.read_text();digest=core.sha(text)
        if ledger['processed'].get(path.name)==digest:continue
        try:
            review=response_json(text)
            if review is not None:
                saved=core.record_review(state,review);messages.append(brief(core.read_json(saved)))
        except (core.SidecarError,ValueError,KeyError,TypeError) as error:
            messages.append('FPL research response rejected ('+type(error).__name__+'). No team changes. Private diagnostic retained for operator review.')
            core.atomic_json(state/'rejected'/(digest+'.json'),{'artifact':path.name,'reason':str(error)[:500]})
        ledger['processed'][path.name]=digest
        with core.locked(state):core.atomic_json(ledger_path,ledger)
    day=core.clock().date().isoformat()
    if evaluate and ledger['evaluated_day']!=day:
        for r in core.evaluate_live(state):
            messages.append(f"FPL weekly comparison GW{r['gameweek']}: model {r['model']['net_points']}, hybrid {r['hybrid']['net_points']}, actual {r['owner_actual']['net_points']} net points. Frozen weekly recommendations, not a season-long transfer-policy backtest.")
        ledger['evaluated_day']=day
        with core.locked(state):core.atomic_json(ledger_path,ledger)
    return messages


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--state-dir',type=Path,default=core.DEFAULT_STATE);parser.add_argument('--output-dir',type=Path);parser.add_argument('--no-evaluate',action='store_true');a=parser.parse_args()
    try:
        if a.output_dir is None:
            config=core.read_json(a.state_dir/'review-job.json');job=config['job_id']
            if not re.fullmatch('[a-f0-9]{12,32}',job):raise core.SidecarError('Invalid configured research job ID')
            a.output_dir=Path(os.environ.get('HERMES_HOME',str(Path.home()/'.hermes')))/'cron/output'/job
        for message in sweep(a.state_dir,a.output_dir,evaluate=not a.no_evaluate):print(message+'\n')
        return 0
    except Exception as error:
        print('FPL review validation/evaluation failed: '+type(error).__name__,file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
