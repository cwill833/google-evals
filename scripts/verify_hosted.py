"""Probe a deployed ADK fixture: IAM, isolation, concurrency and ambiguous timeout.

Does not grade or retry agent POSTs. JSON evidence contains no tokens or private bodies.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

import httpx
from google.genai import types

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from adk_eval_harness.remote import RemoteRunner,RemoteError,RemoteTimeoutError
from examples.load.agent import create_agent


def message(cid,delay=None):
    data={'case_id':cid,'payload':'Hosted isolation verification'}
    if delay is not None:data['delay']=delay
    return types.Content(role='user',parts=[types.Part(text=json.dumps(data))])


async def verify(hosted):
    async with httpx.AsyncClient(follow_redirects=False,timeout=120,trust_env=False) as client:
        anonymous=await client.get(hosted['url']+'/eval/metadata')
    async def conversation(index):
        runner=RemoteRunner(agent=create_agent(),app_name='load',hosted=hosted)
        try:
            session=await runner.create_session(app_name='load',user_id='shared-logical-user',state={})
            values=[]
            for _ in range(3):
                events=[e async for e in runner.run_async(user_id='shared-logical-user',session_id=session.id,new_message=message(f'concurrent-{index}'))]
                values.append(json.loads(events[-1].content.parts[0].text)['turn'])
            snapshot=await runner.get_session(app_name='load',user_id='shared-logical-user',session_id=session.id)
            return {'values':values,'remote_user_id':session.user_id,'session_id':session.id,
                    'final_count':snapshot.state['user:count'],'transport':runner.transport_stats,
                    'metadata':runner.observed_metadata}
        finally:await runner.close()
    concurrent=await asyncio.gather(*(conversation(i) for i in range(4)))
    # Establish a session normally, then bound just the run request. A timeout is an
    # unknown remote outcome and MUST NOT be interpreted as a safe retry signal.
    runner=RemoteRunner(agent=create_agent(),app_name='load',hosted=hosted,turn_timeout=180)
    try:
        session=await runner.create_session(app_name='load',user_id='timeout-probe',state={})
        runner.turn_timeout=5
        failure=None
        timed_out=False
        started=time.monotonic()
        try:
            async for _ in runner.run_async(user_id='timeout-probe',session_id=session.id,new_message=message('timeout-probe',delay=15)):
                pass
        except (RemoteError,TimeoutError) as exc:
            failure=type(exc).__name__
            timed_out=isinstance(exc,(RemoteTimeoutError,TimeoutError))
        timeout={'error_class':failure,'run_posts':runner.transport_stats['run_posts'],
                 'client_timeout':timed_out,'elapsed_seconds':time.monotonic()-started,
                 'outcome':'unknown; no retry issued'}
    finally:await runner.close()
    checks={'unauthenticated_denied':anonymous.status_code in (401,403),
            'all_conversations_stateful':all(x['values']==[1,2,3] and x['final_count']==3 for x in concurrent),
            'separate_remote_users':len({x['remote_user_id'] for x in concurrent})==4,
            'separate_sessions':len({x['session_id'] for x in concurrent})==4,
            'exact_remote_run_requests':all(x['transport']['run_posts']==3 for x in concurrent),
            'timeout_no_retry':timeout['client_timeout'] and timeout['run_posts']==1}
    return {'passed':all(checks.values()),'checks':checks,'anonymous_status':anonymous.status_code,
            'concurrent_cases':concurrent,'timeout_probe':timeout}


def main():
    p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--deployment-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise ValueError('Output must be new')
    result=asyncio.run(verify({'url':a.url,'auth':'gcloud','deployment_manifest':a.deployment_manifest}))
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'passed':result['passed'],'checks':result['checks']}));return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
