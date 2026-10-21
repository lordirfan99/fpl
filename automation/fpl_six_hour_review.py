#!/usr/bin/env python3
"""One advisory review per GW at official deadline minus six hours."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).parent))
import fpl_hybrid_review as core  # noqa: E402
import fpl_review_bridge as bridge  # noqa: E402

RESEARCH_JOB='64db3292a611'
PUBLISH_JOB='20bc00a9c6bb'


def slots(events,at):
    for event in sorted(events,key=lambda e:core.parsed(e['deadline_time'])):
        deadline=core.parsed(event['deadline_time']);start=deadline-dt.timedelta(hours=6)
        if deadline<=at:continue
        # Do not reschedule an in-flight research/publication occurrence.
        if start<=at<start+dt.timedelta(minutes=20):return None
        if start>at:return {'gw':int(event['id']),'deadline':deadline.isoformat(),'review_at':start.isoformat(),'publish_at':(start+dt.timedelta(minutes=5)).isoformat()}
    return None


def research_due(deadline,at):
    start=core.parsed(deadline)-dt.timedelta(hours=6)
    return start<=at<=start+dt.timedelta(minutes=10)


def official_events():
    value=core.public_json('bootstrap-static/')
    if not isinstance(value,dict) or not isinstance(value.get('events'),list):raise core.SidecarError('Official event calendar unavailable')
    return value['events']


def collect(state=core.DEFAULT_STATE):
    at=core.clock()
    upcoming=sorted([e for e in official_events() if core.parsed(e['deadline_time'])>at],key=lambda e:core.parsed(e['deadline_time']))
    if not upcoming or not research_due(upcoming[0]['deadline_time'],at):
        return {'review_already_recorded':True,'six_hour_only':True}
    snap=core.capture(2797967)
    if not research_due(snap['deadline'],core.clock()):return {'review_already_recorded':True,'six_hour_only':True}
    # Distinct policy identity ensures old daily reviews cannot suppress T-6h.
    snap['phase']='T6';snap['daily_bucket']=f"GW{snap['gameweek']}:six-hour-only"
    core.monitor(state,snap)
    return core.context(state)


def publish(state=core.DEFAULT_STATE,output_dir=None):
    state=Path(state);latest=state/'latest_request.json'
    if not latest.exists():return ''
    req=core.read_json(latest)
    if req.get('phase')!='T6':return ''
    start=core.parsed(req['deadline'])-dt.timedelta(hours=6);at=core.clock()
    if not start<=at<=start+dt.timedelta(minutes=30):return ''
    key=f"{req['gameweek']}:{req['deadline']}";ledger_path=state/'six-hour-delivery.json'
    with core.locked(state):
        ledger=core.read_json(ledger_path) if ledger_path.exists() else {}
        if key in ledger:return ''
        if output_dir is None:
            output_dir=Path.home()/'.hermes/cron/output'/RESEARCH_JOB
        candidates=sorted(Path(output_dir).glob('*.md'),key=lambda p:p.stat().st_mtime,reverse=True)
        review=None
        for path in candidates:
            if path.stat().st_mtime<core.parsed(req['captured_at']).timestamp()-60:continue
            if path.stat().st_size>4_000_000:continue
            try:value=bridge.response_json(path.read_text())
            except (ValueError,core.SidecarError):continue
            if value and value.get('event_key')==req['event_key']:
                review=value;break
        # Release the state lock before record_review takes its own lock.
    try:
        if review is None:
            message=f"FPL GW{req['gameweek']} six-hour review unavailable: no valid completed research response. No team changes."
            status='unavailable'
        else:
            path=core.record_review(state,review);message=bridge.brief(core.read_json(path));status='published'
    except (ValueError,KeyError,core.SidecarError) as error:
        core.atomic_json(state/'six-hour-errors'/(req['event_key']+'.json'),{'error':type(error).__name__,'reason':str(error)[:500]})
        message=f"FPL GW{req['gameweek']} six-hour review could not be validated. No team changes."
        status='validation_failed'
    with core.locked(state):
        ledger=core.read_json(ledger_path) if ledger_path.exists() else {}
        if key in ledger:return ''
        ledger[key]={'status':status,'event_key':req['event_key'],'recorded_at':core.stamp()}
        core.atomic_json(ledger_path,ledger)
    return message


def plan(state=core.DEFAULT_STATE,api=None,events=None,at=None):
    """Re-arm two EXISTING native one-shots, never create jobs or run an LLM."""
    at=at or core.clock();events=events if events is not None else official_events()
    target=slots(events,at)
    if target is None:return None
    if api is None:
        sys.path.insert(0,str(Path.home()/'.hermes/hermes-agent'))
        from cron import jobs as api
    jobs={}
    for job_id in [RESEARCH_JOB,PUBLISH_JOB]:
        job=api.get_job(job_id)
        if not isinstance(job,dict):raise core.SidecarError('Managed review jobs missing; no jobs were created')
        jobs[job_id]=job
    # An owner pause remains a pause; the silent planner must not undo it.
    if any(j.get('state')=='paused' for j in jobs.values()):return None
    if any(j.get('run_claim') or j.get('fire_claim') for j in jobs.values()):return None
    for job_id,when in [(RESEARCH_JOB,target['review_at']),(PUBLISH_JOB,target['publish_at'])]:
        job=jobs[job_id];schedule=job.get('schedule') or {}
        if schedule.get('kind')!='once':raise core.SidecarError('Managed research jobs must be native one-shots')
        existing=schedule.get('run_at')
        if existing and core.parsed(existing)==core.parsed(when) and job.get('enabled'):
            continue
        api.rearm_oneshot(job_id,when)
        observed=api.get_job(job_id)
        if not observed or not observed.get('enabled') or core.parsed(observed['schedule']['run_at'])!=core.parsed(when):raise core.SidecarError('One-shot schedule readback mismatch')
    core.atomic_json(Path(state)/'six-hour-policy.json',{'mode':'one_review_per_GW_at_T_minus_6h','target':target,'research_job':RESEARCH_JOB,'publication_job':PUBLISH_JOB,'updated_at':core.stamp(at)})
    return target


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['collect','publish','plan']);a=p.parse_args()
    if a.command=='collect':print(json.dumps(collect(),separators=(',',':')))
    elif a.command=='publish':
        message=publish()
        if message:print(message)
    else:plan()  # Healthy deadline maintenance is always silent.

if __name__=='__main__':main()
