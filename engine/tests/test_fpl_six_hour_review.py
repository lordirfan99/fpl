import copy
import datetime as dt
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'automation'))
import fpl_six_hour_review as six  # noqa: E402
import test_fpl_hybrid_review as fixtures  # noqa: E402

T=six.core.parsed('2026-10-23T11:30:00Z')
EVENTS=[{'id':8,'deadline_time':'2026-10-23T17:30:00Z'},{'id':9,'deadline_time':'2026-10-31T11:00:00Z'}]


def test_official_deadlines_not_fixed_weekday():
    a=six.slots(EVENTS,T-dt.timedelta(days=2));b=six.slots(EVENTS,T+dt.timedelta(minutes=21))
    assert a['review_at']=='2026-10-23T11:30:00+00:00'
    assert a['publish_at']=='2026-10-23T11:35:00+00:00'
    assert b['gw']==9 and b['review_at']=='2026-10-31T05:00:00+00:00'


def test_do_not_rearm_over_current_review():
    assert six.slots(EVENTS,T+dt.timedelta(minutes=5)) is None


@pytest.mark.parametrize('seconds,due',[(-1,False),(0,True),(600,True),(601,False),(3600,False)])
def test_single_research_window(seconds,due):
    assert six.research_due(EVENTS[0]['deadline_time'],T+dt.timedelta(seconds=seconds)) is due


def test_collection_outside_window_does_not_touch_account(monkeypatch,tmp_path):
    monkeypatch.setattr(six.core,'clock',lambda:T-dt.timedelta(days=1))
    monkeypatch.setattr(six,'official_events',lambda:EVENTS)
    monkeypatch.setattr(six.core,'capture',lambda *_:pytest.fail('No account/model collection outside T6'))
    assert six.collect(tmp_path)['review_already_recorded']
    assert not (tmp_path/'latest_request.json').exists()


def test_collection_creates_new_t6_identity(monkeypatch,tmp_path):
    monkeypatch.setattr(six.core,'clock',lambda:T)
    monkeypatch.setattr(six,'official_events',lambda:EVENTS)
    snap=fixtures.snapshot();snap['deadline']=EVENTS[0]['deadline_time']
    monkeypatch.setattr(six.core,'capture',lambda *_:copy.deepcopy(snap))
    monkeypatch.setattr(six.core,'context',lambda state:six.core.read_json(state/'latest_request.json'))
    req=six.collect(tmp_path)
    assert req['phase']=='T6'
    assert six.core.read_json(tmp_path/'snapshots'/(req['event_key']+'.json'))['snapshot']['daily_bucket']=='GW8:six-hour-only'


def test_publisher_is_silent_outside_window(monkeypatch,tmp_path):
    monkeypatch.setattr(six.core,'clock',lambda:T-dt.timedelta(days=1))
    six.core.atomic_json(tmp_path/'latest_request.json',{'phase':'T6','deadline':EVENTS[0]['deadline_time']})
    assert six.publish(tmp_path)==''
    assert not (tmp_path/'six-hour-delivery.json').exists()


def test_daily_legacy_request_never_published(tmp_path):
    six.core.atomic_json(tmp_path/'latest_request.json',{'phase':'daily'})
    assert six.publish(tmp_path)==''


def test_valid_t6_review_published_only_once(monkeypatch,tmp_path):
    monkeypatch.setattr(six.core,'clock',lambda:T)
    snap=fixtures.snapshot();snap.update(phase='T6',deadline=EVENTS[0]['deadline_time'])
    req=six.core.monitor(tmp_path,snap)
    output=tmp_path/'outputs';output.mkdir()
    path=output/'completed.md';path.write_text('# Cron\n\n## Response\n'+json.dumps(fixtures.review(req)))
    os.utime(path,(T.timestamp(),T.timestamp()))
    monkeypatch.setattr(six.core,'clock',lambda:T+dt.timedelta(minutes=5))
    message=six.publish(tmp_path,output)
    assert 'AGREE' in message and 'ADVISORY ONLY' in message
    assert six.publish(tmp_path,output)==''
    assert len(six.core.read_json(tmp_path/'six-hour-delivery.json'))==1
    assert (tmp_path/'reviews'/(req['event_key']+'.json')).exists()


class NativeAPI:
    def __init__(self):
        self.jobs={j:{'enabled':True,'state':'scheduled','schedule':{'kind':'once','run_at':when}} for j,when in [(six.RESEARCH_JOB,'2026-10-23T11:30:00Z'),(six.PUBLISH_JOB,'2026-10-23T11:35:00Z')]}
        self.calls=[]
    def get_job(self,key):return copy.deepcopy(self.jobs.get(key))
    def rearm_oneshot(self,key,when):
        self.calls.append((key,when));self.jobs[key].update(enabled=True,state='scheduled',schedule={'kind':'once','run_at':when})


def test_existing_schedule_idempotent_and_next_gw_automatically_rearmed(tmp_path):
    api=NativeAPI()
    six.plan(tmp_path,api,EVENTS,T-dt.timedelta(days=1));assert api.calls==[]
    for j in api.jobs.values():j.update(enabled=False,state='completed')
    target=six.plan(tmp_path,api,EVENTS,T+dt.timedelta(minutes=21))
    assert target['gw']==9 and len(api.calls)==2
    six.plan(tmp_path,api,EVENTS,T+dt.timedelta(minutes=22));assert len(api.calls)==2


def test_owner_pause_is_never_undone(tmp_path):
    api=NativeAPI();api.jobs[six.RESEARCH_JOB].update(enabled=False,state='paused')
    assert six.plan(tmp_path,api,EVENTS,T+dt.timedelta(days=1)) is None
    assert api.calls==[]


def test_missing_jobs_fail_closed_without_creating_jobs(tmp_path):
    api=NativeAPI();api.jobs.clear()
    with pytest.raises(six.core.SidecarError,match='no jobs were created'):
        six.plan(tmp_path,api,EVENTS,T-dt.timedelta(days=1))
