import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'jobs'))
import post_gw_audit as audit  # noqa: E402
import post_gw_review as review  # noqa: E402


def sample(multiplier=3,chip='3xc'):
    ids=[124]+list(range(1,15))
    actual={pid:(5 if pid==124 else 3) for pid in ids}
    for pid,points in zip(ids[11:],[4,3,2,1]):actual[pid]=points
    minutes={pid:90 for pid in ids}
    plan={'chip':None,'captain':{'id':124},'target_starters':[{'id':pid,'xpts':5.0} for pid in ids[:11]],'bench':[{'id':pid,'xpts':2.0} for pid in ids[11:]],'transfers':[]}
    picks={'active_chip':chip,'picks':[{'element':pid,'position':i+1,'multiplier':multiplier if pid==124 else 1 if i<11 else 0,'is_captain':pid==124,'is_vice_captain':pid==1} for i,pid in enumerate(ids)]}
    elements={pid:{'id':pid,'web_name':'Groß' if pid==124 else f'P{pid}','status':'a'} for pid in ids}
    return plan,actual,minutes,picks,elements


def test_official_triple_captain_overrides_null_proposal_chip():
    p,a,m,picks,els=sample();result=audit.build_audit(p,a,m,els,gw_points=45,official_picks=picks);s=result['summary'];text='\n'.join(result['lines'])
    assert s['xi_actual']==35 and s['official_weighted_total']==45
    assert s['captain_multiplier']==3 and s['captain_total_points']==15 and s['captain_extra_points']==10
    assert s['actual_chip']=='3xc' and s['planned_chip'] is None
    assert 'incremental points vs normal captain: 5' in text
    assert 'mismatch' in text and 'cause undetermined' in text
    assert 'variance/luck' not in text and 'process working' not in text
    assert s['injury_timing_verified'] is False
    assert 'not a recoverable-gain estimate' in text


@pytest.mark.parametrize('multiplier,chip,gap',[(2,None,-4),(3,'3xc',-8)])
def test_captain_opportunity_is_extra_multiplier_only(multiplier,chip,gap):
    p,a,m,picks,els=sample(multiplier,chip);a[1]=9
    s=audit.build_audit(p,a,m,els,official_picks=picks)['summary']
    assert s['captain_hindsight_gap']==gap


def test_actual_vice_fallback_is_effective_captain():
    p,a,m,picks,els=sample();a[124]=0;m[124]=0;picks['picks'][0]['multiplier']=0;picks['picks'][1]['multiplier']=3
    ctx=audit.captain_context(p,picks);assert ctx['id']==1 and ctx['multiplier']==3
    assert audit.build_audit(p,a,m,els,official_picks=picks)['summary']['captain_total_points']==9


def test_official_no_chip_overrides_planned_triple_captain():
    p,a,m,picks,els=sample(2,None);p['chip']='3xc'
    s=audit.build_audit(p,a,m,els,official_picks=picks)['summary']
    assert s['actual_chip'] is None and s['captain_multiplier']==2


def test_missing_official_picks_not_claimed_verified():
    p,a,m,_,els=sample();r=audit.build_audit(p,a,m,els)
    assert 'unverified' in '\n'.join(r['lines']) and r['summary']['official_weighted_total'] is None


def test_missing_actuals_not_converted_to_zero():
    p,a,m,picks,els=sample();del a[124]
    s=audit.build_audit(p,a,m,els,official_picks=picks)['summary']
    assert s['xi_actual'] is None and s['residual'] is None and s['official_weighted_total'] is None


def test_history_current_and_legacy_compatibility():
    assert review.history_rows({'current':[{'event':7}]})==[{'event':7}]
    assert review.history_rows({'history':[{'event':6}]})==[{'event':6}]
    assert review.history_rows({'current':None})==[]


def test_report_only_does_not_write_history_residuals_or_forecasts(tmp_path,monkeypatch,capsys):
    p,a,m,picks,els=sample();(tmp_path/'config').mkdir();d=tmp_path/'data/processed';d.mkdir(parents=True)
    (tmp_path/'config/settings.json').write_text(json.dumps({'team_id':1}))
    (d/'plan_gw7.json').write_text(json.dumps(p))
    (d/'predictions_gw7.json').write_text(json.dumps({'projection_version':'competitive-v4.0-recency-v1','projection_features':'recency-v1','players':[{'id':pid,'name':str(pid),'xpts':4} for pid in a]}))
    (d/'residuals_recency_v1.csv').write_text('original untouched calibration\n')
    (d/'v42_shadow_gw7.json').write_text('{}')
    before={str(f.relative_to(tmp_path)):f.read_bytes() for f in tmp_path.rglob('*') if f.is_file()}
    class Client:
        def entry_history(self,_entry):
            return {'current':[{'event':6,'overall_rank':5892305},{'event':7,'points':45,'total_points':385,'overall_rank':6637328,'event_transfers_cost':0}]}
        def entry_picks(self,_entry,_gw):return copy.deepcopy(picks)
    def fetch(url):
        if 'bootstrap-static' in url:return {'elements':list(els.values()),'events':[{'id':7,'finished':True,'data_checked':True}]}
        return {'elements':[{'id':pid,'stats':{'total_points':pts,'minutes':m[pid]}} for pid,pts in a.items()]}
    def forbidden(*args,**kwargs):raise AssertionError('report-only attempted data mutation')
    monkeypatch.setattr(review,'BASE',str(tmp_path));monkeypatch.setattr(review,'FPLClient',Client);monkeypatch.setattr(review,'fetch',fetch)
    monkeypatch.setattr(review,'write_event_rows',forbidden);monkeypatch.setattr(review,'merge_residual_rows',forbidden)
    monkeypatch.setattr(sys,'argv',['post_gw_review.py','7','--report-only'])
    review.main();text=capsys.readouterr().out
    after={str(f.relative_to(tmp_path)):f.read_bytes() for f in tmp_path.rglob('*') if f.is_file()}
    assert before==after
    assert 'official points: 45' in text and 'x 3 = 15' in text and '+745023' in text
    assert 'not in entry history yet' not in text
    assert 'Report-only:' in text and 'competitive-v4.0-recency-v1' in text
