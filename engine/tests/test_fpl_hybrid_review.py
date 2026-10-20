import copy
import datetime as dt
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'automation'))
import fpl_hybrid_review as core  # noqa: E402
import fpl_review_bridge as bridge  # noqa: E402


def snapshot():
    types=[1,2,2,2,3,3,3,3,4,4,4,1,2,2,3]
    picks=[{'element':i,'position':i,'is_captain':i==9,'is_vice_captain':i==10} for i in range(1,16)]
    return {'captured_at':core.stamp(),'gameweek':8,'entry':123,'deadline':core.stamp(core.clock()+dt.timedelta(days=2)),
            'plan_id':'a'*64,'source_kind':'pending','model_version':'learned-v5.0','model':{'picks':picks,'hit_cost':0,'chip':None},
            'account':{'picks':picks,'transfers':{'bank':10,'limit':1,'made':0}},
            'elements':[{'id':i,'web_name':f'P{i}','element_type':typ,'team':i//3+1,'now_cost':50,'status':'a','news':''} for i,typ in enumerate(types,1)],
            'fixtures':[{'id':1,'event':8,'team_h':1,'team_a':2,'kickoff_time':core.stamp(core.clock()+dt.timedelta(days=2))}],
            'completed_match_ids':[1,2],'phase':'daily','daily_bucket':'2026-10-20'}


def review(request):
    return {k:request[k] for k in ['event_key','snapshot_id']}|{'verdict':'agree','suggested_action':'retain','checked_at':core.stamp(),
        'uncertainty':0.5,'summary':'Keep the model recommendation pending final team news.',
        'affected_players':[{'id':9,'assumption':'Current injury status remains available.'}],
        'evidence':[{'url':'https://fantasy.premierleague.com/api/bootstrap-static/','published_at':None,'checked_at':core.stamp()}]}


def test_monitor_ignores_timestamps_and_projection_noise(tmp_path):
    s=snapshot();a=core.monitor(tmp_path,s);b=copy.deepcopy(s);b['captured_at']=core.stamp(core.clock()+dt.timedelta(seconds=10));b['projection']=[{'xpts':5.111}]
    assert core.monitor(tmp_path,b)==a
    assert len(list((tmp_path/'snapshots').glob('*.json')))==1
    assert (tmp_path/'snapshots').stat().st_mode&0o777==0o700


@pytest.mark.parametrize('field',['price','news','day','phase','account','fixture','match'])
def test_material_triggers(tmp_path,field):
    s=snapshot();a=core.monitor(tmp_path,s);t=copy.deepcopy(s)
    if field=='price':t['elements'][0]['now_cost']+=1
    if field=='news':t['elements'][0]['news']='Injury doubt'
    if field=='day':t['daily_bucket']='2026-10-21'
    if field=='phase':t['phase']='T24'
    if field=='account':t['account']['transfers']['made']=1
    if field=='fixture':t['fixtures'][0]['event']=9
    if field=='match':t['completed_match_ids'].append(3)
    assert core.monitor(tmp_path,t)['event_key']!=a['event_key']


def test_deadline_phase_boundaries():
    at=core.clock()
    assert [core.phase(at+dt.timedelta(hours=h),at) for h in [25,24,2,1]]==['daily','T24','T2','T1']


def test_record_bound_immutable_and_idempotent(tmp_path):
    r=review(core.monitor(tmp_path,snapshot()));p=core.record_review(tmp_path,r)
    assert core.record_review(tmp_path,r)==p
    assert core.read_json(p)['prospective_eligible']
    assert not core.read_json(p)['hybrid_adjusted']
    assert p.stat().st_mode&0o777==0o600
    with pytest.raises(core.SidecarError,match='Immutable'):core.record_review(tmp_path,{**r,'summary':'A different decision cannot overwrite this frozen review.'})


def test_superseded_review_cannot_record(tmp_path):
    s=snapshot();r=review(core.monitor(tmp_path,s));s['phase']='T24';core.monitor(tmp_path,s)
    with pytest.raises(core.SidecarError,match='Superseded'):core.record_review(tmp_path,r)


@pytest.mark.parametrize('patch',[{'verdict':'execute'},{'uncertainty':float('nan')},{'uncertainty':True},{'execute':True},{'affected_players':[{'id':999,'assumption':'unknown player'}]},{'checked_at':'no date'},{'alternative':{'transfers':[]}}])
def test_reject_bad_reviews(tmp_path,patch):
    r=review(core.monitor(tmp_path,snapshot()));r.update(patch)
    with pytest.raises((core.SidecarError,ValueError)):core.record_review(tmp_path,r)


def test_future_source_rejected(tmp_path):
    r=review(core.monitor(tmp_path,snapshot()));r['evidence'][0]['published_at']=core.stamp(core.clock()+dt.timedelta(days=1))
    with pytest.raises(core.SidecarError,match='Future'):core.record_review(tmp_path,r)


def test_late_review_advisory_not_scored(tmp_path):
    s=snapshot();s['deadline']=core.stamp(core.clock()-dt.timedelta(seconds=1));p=core.record_review(tmp_path,review(core.monitor(tmp_path,s)))
    assert core.read_json(p)['prospective_eligible'] is False


def test_actual_plan_schema_and_newest_source():
    at=core.clock();p={'gw':8,'plan_id':'a'*64,'generated_at':core.stamp(at-dt.timedelta(hours=1)),'status':'executed'}
    d={**p,'generated_at':core.stamp(at-dt.timedelta(minutes=10))}
    selected,kind=core.choose_plan({'pending_plan.json':p,'dashboard_plan.json':d},8,at)
    assert selected==d and kind=='dashboard_preview'
    assert core.choose_plan({'pending_plan.json':p},8,at)[1]=='pending_executed'
    with pytest.raises(core.SidecarError):core.choose_plan({'pending_plan.json':{**p,'generated_at':core.stamp(at-dt.timedelta(hours=7))}},8,at)


def test_remote_probe_source_is_deployed_layout_not_repo_layout():
    assert "data/processed" in core._REMOTE_SCRIPT
    assert "from fpl_client import FPLClient" in core._REMOTE_SCRIPT
    assert 'credentials.env' not in core._REMOTE_SCRIPT and 'engine.execution' not in core._REMOTE_SCRIPT


def test_parse_final_response_only():
    payload={'event_key':'real'};text='# Job\n## Prompt\n{"event_key":"wrong"}\n## Response\n\n'+json.dumps(payload)
    assert bridge.response_json(text)==payload
    assert bridge.response_json(text.rsplit('## Response',1)[0]+'## Response\n\n```json\n'+json.dumps(payload)+'\n```')==payload
    with pytest.raises((ValueError,core.SidecarError)):bridge.response_json(text+' trailing prose')


def test_bridge_validates_once_without_team_side_effects(tmp_path):
    state=tmp_path/'state';outputs=tmp_path/'outputs';outputs.mkdir();req=core.monitor(state,snapshot());r=review(req)
    (outputs/'one.md').write_text('# Cron\n## Response\n\n'+json.dumps(r));messages=bridge.sweep(state,outputs,evaluate=False)
    assert len(messages)==1 and 'ADVISORY ONLY' in messages[0]
    assert bridge.sweep(state,outputs,evaluate=False)==[]
    assert set(p.name for p in state.iterdir())=={'.lock','snapshots','latest_request.json','reviews','bridge-ledger.json'}


def test_failed_review_retries_with_fresh_immutable_input(tmp_path,monkeypatch):
    at=core.clock();s=snapshot();s['captured_at']=core.stamp(at);a=core.monitor(tmp_path,s)
    monkeypatch.setattr(core,'clock',lambda:at+dt.timedelta(minutes=61));s['captured_at']=core.stamp();b=core.monitor(tmp_path,s)
    assert a['event_key']!=b['event_key'] and b['attempt']==1
    assert len(list((tmp_path/'snapshots').glob('*.json')))==2
