#!/usr/bin/env python3
"""Advisory-only FPL research sidecar; never changes the account or pending plan."""
from __future__ import annotations
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse

UTC = dt.timezone.utc
DEFAULT_STATE = Path.home()/'.local/state/fpl-hybrid-review'
PUBLIC_BASE = 'https://fantasy.premierleague.com/api'

class SidecarError(RuntimeError):
    pass

def clock():
    return dt.datetime.now(UTC)

def stamp(value=None):
    return (value or clock()).isoformat()

def parsed(value):
    try:
        result = dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        assert result.tzinfo is not None
        return result.astimezone(UTC)
    except (ValueError, TypeError, AssertionError) as e:
        raise SidecarError('Missing or invalid timezone-aware timestamp') from e

def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',',':'), allow_nan=False).encode()).hexdigest()

def read_json(path):
    return json.loads(Path(path).read_text())

def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True); path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(value,f,sort_keys=True,indent=2,allow_nan=False); f.write('\n')
        os.chmod(temporary,0o600); os.replace(temporary,path)
    finally:
        with contextlib.suppress(FileNotFoundError): os.unlink(temporary)

@contextlib.contextmanager
def locked(state):
    state=Path(state); state.mkdir(parents=True,exist_ok=True); state.chmod(0o700)
    with (state/'.lock').open('a') as f:
        os.chmod(state/'.lock',0o600); fcntl.flock(f,fcntl.LOCK_EX); yield

def retry(function):
    for attempt in range(2):
        try: return function()
        except (OSError, ValueError, subprocess.SubprocessError):
            if attempt: raise
            time.sleep(0.5)

_REMOTE_SCRIPT = '''import sys,json,datetime,pathlib
B=pathlib.Path('/opt/fpl-autopilot');sys.path.insert(0,str(B/'execution'))
from fpl_client import FPLClient
out={'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
for name in ['pending_plan.json','dashboard_plan.json']:
 p=B/'data/processed'/name
 if p.exists():out[name]=json.loads(p.read_text())
t=FPLClient().my_team(ENTRY)
out['account']={'picks':t['picks'],'transfers':t['transfers']}
print(json.dumps(out))
'''

def remote_probe(entry):
    host=os.environ.get('FPL_REVIEW_SSH_HOST','100.72.109.7'); user=os.environ.get('FPL_REVIEW_SSH_USER','irfan')
    identity=os.environ.get('FPL_REVIEW_SSH_IDENTITY',str(Path.home()/'.ssh/pc_irfan_key'))
    if not re.fullmatch(r'[A-Za-z0-9_.:-]+',host) or not re.fullmatch(r'[A-Za-z0-9_.-]+',user): raise SidecarError('Invalid SSH identity configuration')
    if not Path(identity).is_absolute() or not Path(identity).is_file(): raise SidecarError('SSH identity unavailable')
    command=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=8','-i',identity,f'{user}@{host}',
             'cd /opt/fpl-autopilot && sudo -u fpl env HOME=/home/fpl FPL_AUTOPILOT_HOME=/opt/fpl-autopilot .venv/bin/python -']
    def fetch():
        result=subprocess.run(command,input=_REMOTE_SCRIPT.replace('ENTRY',str(int(entry))),text=True,capture_output=True,timeout=20,check=True)
        return json.loads(result.stdout)
    result=retry(fetch)
    if abs((clock()-parsed(result['captured_at'])).total_seconds())>180: raise SidecarError('Remote account capture is stale')
    if len(result.get('account',{}).get('picks',[]))!=15: raise SidecarError('Authenticated account must have 15 picks')
    return result

def public_json(path):
    def fetch():
        req=urllib.request.Request(PUBLIC_BASE+'/'+path.lstrip('/'),headers={'User-Agent':'FPL-hybrid-review/1'})
        with urllib.request.urlopen(req,timeout=12) as r:return json.load(r)
    return retry(fetch)

def choose_plan(probe,gw,at):
    candidates=[]
    for name in ['pending_plan.json','dashboard_plan.json']:
        p=probe.get(name)
        if not isinstance(p,dict) or p.get('gw')!=gw:continue
        generated=parsed(p.get('generated_at'))
        if -300 <= (at-generated).total_seconds() <= 6*3600 and re.fullmatch('[0-9a-f]{64}',str(p.get('plan_id',''))):
            candidates.append((generated,name,p))
    if not candidates:raise SidecarError('No fresh same-GW model plan (maximum age 6h)')
    _,name,p=max(candidates,key=lambda row:row[0])
    kind='dashboard_preview' if name=='dashboard_plan.json' else ('pending_executed' if p.get('status')=='executed' else 'pending')
    return p,kind

def scoring_module():
    sys.path.insert(0,str(Path(__file__).parent))
    import fpl_review_scoring
    return fpl_review_scoring

def metadata(elements):
    return {int(e['id']):{'position':int(e['element_type']),'club':int(e['team'])} for e in elements}

def normalize_plan(plan):
    order={'GKP':1,'DEF':2,'MID':3,'FWD':4}
    starters=sorted(plan['target_starters'],key=lambda p:order[p['position']])
    bench=plan['bench']; keepers=[p for p in bench if p['position']=='GKP']; outfield=[p for p in bench if p['position']!='GKP']
    rows=starters+keepers+outfield
    return {'picks':[{'element':p['id'],'position':i+1,'is_captain':p['id']==plan['captain']['id'],'is_vice_captain':p['id']==plan['vice']['id']} for i,p in enumerate(rows)],
            'hit_cost':4*sum(bool(t.get('hit')) for t in plan.get('transfers',[])), 'chip':plan.get('chip')}

def phase(deadline,at):
    hours=(deadline-at).total_seconds()/3600
    return 'T1' if hours<=1 else 'T2' if hours<=2 else 'T24' if hours<=24 else 'daily'

def capture(entry):
    at=clock(); b=public_json('bootstrap-static/'); fixtures=public_json('fixtures/')
    future=[e for e in b['events'] if parsed(e['deadline_time'])>at]
    if not future:raise SidecarError('No upcoming gameweek; season monitoring should be paused')
    event=min(future,key=lambda e:parsed(e['deadline_time'])); gw=int(event['id']); probe=remote_probe(entry)
    plan,kind=choose_plan(probe,gw,at); model=normalize_plan(plan)
    owned={p['element'] for p in probe['account']['picks']}; ids=owned|{p['element'] for p in model['picks']}
    extra=(plan.get('competitive') or {}).get('critical_missing',[])[:5]
    ids.update(int(p['element']) for p in extra if p.get('element') is not None)
    fields='id web_name element_type team now_cost status chance_of_playing_next_round news news_added minutes starts total_points goals_scored assists clean_sheets goals_conceded saves bonus expected_goals expected_assists expected_goals_conceded penalties_order corners_and_indirect_freekicks_order direct_freekicks_order'.split()
    elements=[{k:e.get(k) for k in fields} for e in b['elements'] if e['id'] in ids]
    for e in elements:
        minutes=int(e['minutes'] or 0)
        e['xgi_per90']=round((float(e['expected_goals'] or 0)+float(e['expected_assists'] or 0))*90/minutes,3) if minutes else None
        e['xgc_per90']=round(float(e['expected_goals_conceded'] or 0)*90/minutes,3) if minutes else None
    if ids-{e['id'] for e in elements}:raise SidecarError('Official catalogue missing a relevant player')
    scoring_module().validate_picks(model['picks'],metadata(elements))
    scoring_module().validate_picks(probe['account']['picks'],metadata(elements))
    clubs={e['team'] for e in elements}
    relevant=[f for f in fixtures if f.get('event') in range(gw,min(39,gw+3)) and clubs.intersection([f['team_h'],f['team_a']])]
    completed=[f['id'] for f in fixtures if f.get('finished') and clubs.intersection([f['team_h'],f['team_a']])]
    projection=[{k:p.get(k) for k in ['id','name','position','xpts','p_start','expected_minutes','xpts_by_gw']} for p in plan['target_starters']+plan['bench']]
    return {'captured_at':stamp(at),'gameweek':gw,'deadline':event['deadline_time'],'entry':entry,'plan_id':plan['plan_id'],
            'source_kind':kind,'plan_generated_at':plan['generated_at'],'model_version':plan.get('projection_version'),'model':model,
            'account':probe['account'],'projection':projection,'elements':elements,'teams':[{k:t[k] for k in ['id','name','short_name']} for t in b['teams']],
            'fixtures':relevant,'completed_match_ids':completed,'phase':phase(parsed(event['deadline_time']),at),'daily_bucket':at.date().isoformat()}

def trigger_identity(snap):
    health=[{k:e.get(k) for k in ['id','team','now_cost','status','chance_of_playing_next_round','news','news_added']} for e in snap['elements']]
    account={'picks':[{k:p.get(k) for k in ['element','position','is_captain','is_vice_captain']} for p in snap['account']['picks']],
             'transfers':{k:snap['account']['transfers'].get(k) for k in ['bank','limit','made','status']}}
    fixture_fields=['id','event','team_h','team_a','kickoff_time']
    return sha({'gw':snap['gameweek'],'plan':snap['plan_id'],'source_kind':snap['source_kind'],'model':snap['model_version'],
                'account':account,'health':health,'fixtures':[{k:f.get(k) for k in fixture_fields} for f in snap['fixtures']],
                'completed':snap['completed_match_ids'],'day':snap['daily_bucket'],'phase':snap['phase']})

def monitor(state,snap):
    state=Path(state)
    with locked(state):
        latest=state/'latest_request.json'; old=read_json(latest) if latest.exists() else None; base=trigger_identity(snap)
        attempt=0
        if old and old['base_key']==base:
            done=(state/'reviews'/(old['event_key']+'.json')).exists()
            if done or (clock()-parsed(old['captured_at'])).total_seconds()<3600:return old
            attempt=old['attempt']+1
        key=sha({'base':base,'attempt':attempt}); snapshot_id=sha(snap)
        request={'event_key':key,'snapshot_id':snapshot_id,'base_key':base,'attempt':attempt,'captured_at':snap['captured_at'],
                 'gameweek':snap['gameweek'],'deadline':snap['deadline'],'plan_id':snap['plan_id'],'phase':snap['phase']}
        immutable=state/'snapshots'/(key+'.json')
        if immutable.exists():raise SidecarError('Immutable snapshot collision')
        atomic_json(immutable,{'request':request,'snapshot':snap}); atomic_json(latest,request)
        return request

def context(state):
    req=read_json(Path(state)/'latest_request.json'); bundle=read_json(Path(state)/'snapshots'/(req['event_key']+'.json'))
    if (clock()-parsed(req['captured_at'])).total_seconds()>3600:raise SidecarError('Research snapshot older than one hour; run monitor')
    snap=bundle['snapshot']; histories={}
    # Only critical owned players, not the entire player catalogue.
    ids=[p['element'] for p in snap['model']['picks']]
    for pid in ids:
        history=public_json(f'element-summary/{pid}/').get('history',[])
        histories[str(pid)]=[{k:r.get(k) for k in ['round','minutes','total_points','expected_goals','expected_assists']} for r in history[-3:]]
    return {**req,'research_material':snap,'recent_matches':histories,'review_already_recorded':(Path(state)/'reviews'/(req['event_key']+'.json')).exists()}

def validate_review(review,bundle,at=None):
    at=at or clock(); req=bundle['request']; snap=bundle['snapshot']
    required={'event_key','snapshot_id','verdict','suggested_action','checked_at','evidence','affected_players','uncertainty','summary'}
    if not isinstance(review,dict) or not required<=review.keys() or set(review)-required-{'alternative'}:raise SidecarError('Review schema mismatch')
    if any(review[k]!=req[k] for k in ['event_key','snapshot_id']):raise SidecarError('Review/event binding mismatch')
    if review['verdict'] not in {'agree','disagree','insufficient_evidence'} or review['suggested_action'] not in {'retain','recalculate','request_approval','hold'}:raise SidecarError('Invalid review verdict/action')
    if isinstance(review['uncertainty'],bool) or not isinstance(review['uncertainty'],(int,float)) or not math.isfinite(review['uncertainty']) or not 0<=review['uncertainty']<=1:raise SidecarError('Invalid uncertainty heuristic')
    if not isinstance(review['summary'],str) or not 10<=len(review['summary'])<=1800:raise SidecarError('Review summary length invalid')
    checked=parsed(review['checked_at']); captured=parsed(req['captured_at'])
    if checked<captured-dt.timedelta(minutes=5) or checked>at+dt.timedelta(minutes=5) or at-captured>dt.timedelta(hours=2):raise SidecarError('Review timestamps stale or future')
    if not isinstance(review['evidence'],list) or not 1<=len(review['evidence'])<=10:raise SidecarError('Evidence list invalid')
    for e in review['evidence']:
        if not isinstance(e,dict) or set(e)!={'url','published_at','checked_at'}:raise SidecarError('Evidence schema invalid')
        url=urlparse(e['url'])
        if url.scheme!='https' or not url.hostname or url.username or url.password or len(e['url'])>1000:raise SidecarError('Evidence URL invalid')
        when=parsed(e['checked_at'])
        if when>at+dt.timedelta(minutes=5) or when<captured-dt.timedelta(hours=6):raise SidecarError('Evidence check timestamp invalid')
        if e['published_at'] is not None and parsed(e['published_at'])>at+dt.timedelta(minutes=5):raise SidecarError('Future evidence publication')
    allowed={p['id'] for p in snap['elements']}
    if not isinstance(review['affected_players'],list) or len(review['affected_players'])>20:raise SidecarError('Affected players invalid')
    for p in review['affected_players']:
        if not isinstance(p,dict) or set(p)!={'id','assumption'} or type(p['id']) is not int or p['id'] not in allowed or not isinstance(p['assumption'],str) or not 5<=len(p['assumption'])<=500:raise SidecarError('Affected player/assumption invalid')
    if review.get('alternative') is not None:
        alt=review['alternative']
        if not isinstance(alt,dict) or set(alt)!={'picks'}:raise SidecarError('Alternative permits picks only, no transfers/chips')
        scoring_module().validate_picks(alt['picks'],metadata(snap['elements']))
        if {p['element'] for p in alt['picks']}!={p['element'] for p in snap['model']['picks']}:raise SidecarError('Alternative cannot change the model squad')

def record_review(state,review):
    state=Path(state)
    with locked(state):
        key=review.get('event_key','')
        if not re.fullmatch('[0-9a-f]{64}',key):raise SidecarError('Invalid event ID')
        req=read_json(state/'latest_request.json'); bundle=read_json(state/'snapshots'/(key+'.json'))
        if req['event_key']!=key:raise SidecarError('Superseded review; no longer current')
        validate_review(review,bundle); path=state/'reviews'/(key+'.json')
        if path.exists():
            if read_json(path)['review']!=review:raise SidecarError('Immutable review cannot be rewritten')
            return path
        snap=bundle['snapshot']; model=snap['model']; hybrid={**model,'picks':review['alternative']['picks']} if review.get('alternative') else model
        saved={'request':req,'snapshot':snap,'review':review,'model':model,'hybrid':hybrid,'recorded_at':stamp(),
               'prospective_eligible':clock()<parsed(snap['deadline']),'hybrid_adjusted':hybrid!=model}
        atomic_json(path,saved); return path

def evaluate_live(state):
    state=Path(state); events=public_json('bootstrap-static/')['events']; reports=[]
    for ev in events:
        if not ev.get('finished') or not ev.get('data_checked'):continue
        gw=int(ev['id']); target=state/'evaluations'/f'gw{gw}.json'
        if target.exists():continue
        reviews=[read_json(p) for p in (state/'reviews').glob('*.json')]
        eligible=[r for r in reviews if r['snapshot']['gameweek']==gw and r['prospective_eligible'] and parsed(r['recorded_at'])<parsed(ev['deadline_time'])]
        if not eligible:continue
        r=max(eligible,key=lambda r:parsed(r['recorded_at'])); snap=r['snapshot']; meta=metadata(snap['elements'])
        live=public_json(f'event/{gw}/live/'); actual={int(e['id']):e['stats'] for e in live['elements']}
        players={pid:{**m,'minutes':actual[pid]['minutes'],'points':actual[pid]['total_points']} for pid,m in meta.items() if pid in actual}
        hist=public_json(f"entry/{snap['entry']}/history/"); row=next((h for h in hist['current'] if h['event']==gw),None)
        if row is None:raise SidecarError('Official owner result unavailable')
        outcome={'gameweek':gw,'status':'finished','data_checked':True,'deadline':ev['deadline_time'],'players':players,'model':r['model'],'hybrid':r['hybrid'],
                 'owner_actual':{'official':True,'net_points':int(row['points'])-int(row['event_transfers_cost']),'source':PUBLIC_BASE+f"/entry/{snap['entry']}/history/"}}
        result=scoring_module().evaluate(outcome)
        result.update({'event_key':r['request']['event_key'],'plan_id':snap['plan_id'],'scope':'weekly frozen recommendations, not season-long transfer-policy backtest',
                       'owner_actual_is_realized_not_prefrozen':True,'hybrid_adjusted':r['hybrid_adjusted']})
        with locked(state):
            if target.exists():continue
            atomic_json(target,result)
        reports.append(result)
    return reports

def cli():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['monitor','collect','record','status','evaluate'])
    parser.add_argument('--state-dir',type=Path,default=DEFAULT_STATE);parser.add_argument('--input',type=Path);parser.add_argument('--entry',type=int,default=2797967)
    a=parser.parse_args()
    try:
        if a.command=='monitor':
            request=monitor(a.state_dir,capture(a.entry));print(json.dumps({k:request[k] for k in ['event_key','snapshot_id']}))
        elif a.command=='collect':print(json.dumps(context(a.state_dir),separators=(',',':')))
        elif a.command=='record':
            if not a.input:raise SidecarError('--input required')
            print(record_review(a.state_dir,read_json(a.input)))
        elif a.command=='status':print(json.dumps(read_json(a.state_dir/'latest_request.json')))
        else:
            for report in evaluate_live(a.state_dir):print(json.dumps(report))
        return 0
    except Exception as error:
        print('FPL review source/validation failed: '+type(error).__name__+(' '+str(error) if isinstance(error,SidecarError) else ''),file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(cli())
