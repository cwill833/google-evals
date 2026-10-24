"""Repeatable capacity tests. Local scores are synthetic; --native uses Google unchanged."""
import argparse
import copy
import json
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adk_eval_harness.gate import digest, evaluate, read
from adk_eval_harness.runner import VERSIONS, save


def prepare(output, count, turns=3, payload_bytes=1024, *, qps=2, native_metric=None, case_concurrency=1, delay=0):
    output.mkdir(parents=True, exist_ok=False)
    cases, rules = [], {}
    for index in range(count):
        cid = f'load-{index:05d}'
        sentence = f'Inventory record {index}: item is available, quantity 12, location aisle B. This record is for evaluation. '
        payload = (sentence * (payload_bytes // len(sentence) + 1))[:payload_bytes]
        expected = {'case_id':cid, 'turn':turns, 'payload':payload}
        message = json.dumps({'instruction':'Increment the isolated session counter and return JSON containing case_id, turn, and the payload copied verbatim.',
                              'case_id':cid, 'payload':payload, 'delay':delay})
        cases.append({'eval_case_id':cid, 'messages':[message] * turns,
                      'prompt':{'role':'user','parts':[{'text':message}]},
                      'reference':{'response':{'role':'model','parts':[{'text':json.dumps(expected)}]}}})
        rules[cid] = {'min_turns':turns, 'call_counts':{'increment':turns},
                      'json_response_expected':expected,
                      'turns':{str(t):{'state_equals':{'user:count':t+1},
                          'calls':[{'name':'increment','args':{'case_id':cid}}]} for t in range(turns)}}
    save(output/'dataset.json',cases)
    metric = native_metric or ('final_response_match' if turns == 1 else 'multi_turn_task_success')
    (output/'metrics.yaml').write_text(f'metrics_to_run:\n  - {metric}\n')
    save(output/'policy.json',{'schema_version':2,'thresholds':{metric:1},
                              'suites':{'load':{'metrics':[metric],'cases':rules}}})
    save(output/'config.json',{'schema_version':1,'framework':'adk','project_root':str(ROOT),
         'agent':'examples.load.agent:create_agent','agent_factory':True,'app_name':'load_fixture',
         'user_id':'load_user','source_roots':['examples/load','pyproject.toml','uv.lock'],
         'policy':'policy.json','suites':{'load':{'dataset':'dataset.json','metrics':'metrics.yaml'}},
         'turn_timeout':30,'grading_timeout':3600,'qps':qps,'case_concurrency':case_concurrency})
    return metric


def worker(output, count, turns, payload_bytes, case_concurrency=1, delay=0):
    """A separate process provides comparable per-workload peak RSS."""
    metric = prepare(output/'pack',count,turns,payload_bytes,case_concurrency=case_concurrency,delay=delay)
    started=time.monotonic()
    run=output/'run';run.mkdir()
    from adk_eval_harness.config import load
    from adk_eval_harness.runner import generate
    config=load(output/'pack/config.json')
    from adk_eval_harness.validation import validate
    validate(config)
    generate(config,'load',run/'traces.json')
    generation_seconds=time.monotonic()-started
    traces=read(run/'traces.json')
    journal=[json.loads(line) for line in (run/'completed_cases.jsonl').read_text().splitlines()]
    assert sorted(journal,key=lambda c:c['eval_case_id']) == sorted(traces['eval_cases'],key=lambda c:c['eval_case_id'])
    assert [c['eval_case_id'] for c in traces['eval_cases']] == [f'load-{i:05d}' for i in range(count)]
    progress=read(run/'progress.json')
    assert progress['complete'] and progress['peak_active_cases'] <= case_concurrency
    if delay > 0:
        assert progress['peak_active_cases'] == min(case_concurrency,count)
    # Synthetic grades solely exercise gate capacity/integrity. Never present these as Google scores.
    result={'evaluation_dataset':[traces], 'eval_case_results':[
        {'eval_case_index':i,'response_candidate_results':[{'response_index':0,
          'metric_results':{metric:{'metric_name':metric,'score':1.0}}}]} for i in range(count)],
        'summary_metrics':[{'metric_name':metric,'num_cases_total':count,'num_cases_valid':count,'num_cases_error':0}]}
    save(run/'synthetic_results.json',result)
    policy=read(output/'pack/policy.json');save(run/'policy.json',policy)
    manifest={'schema_version':2,'policy_sha256':digest(run/'policy.json'),'versions':VERSIONS,
              'commands':[{'exit_code':0,'kind':'SYNTHETIC_LOAD_ONLY'}],'errors':[],
              'suites':{'load':{'trace':'traces.json','results':'synthetic_results.json',
                       'trace_sha256':digest(run/'traces.json'),'results_sha256':digest(run/'synthetic_results.json')}}}
    save(run/'manifest.json',manifest)
    gate_start=time.monotonic();report=evaluate(run,run/'policy.json');gate_seconds=time.monotonic()-gate_start
    assert report['passed'] and len(report['scores'])==count
    save(run/'gate.json',report)
    # Exact coverage, grader outages, and behavior errors must remain fail-closed at scale.
    controls={}
    for name in ('missing_case','grade_unavailable','wrong_answer'):
        changed=copy.deepcopy(result)
        row=changed['eval_case_results'][-1]
        if name=='missing_case': changed['eval_case_results'].pop()
        elif name=='grade_unavailable':
            row['response_candidate_results'][0]['metric_results'][metric]['error_message']='Injected 429 quota error'
        else: row['response_candidate_results'][0]['metric_results'][metric]['score']=0.0
        save(run/'synthetic_results.json',changed)
        manifest['suites']['load']['results_sha256']=digest(run/'synthetic_results.json')
        save(run/'manifest.json',manifest)
        outcome=evaluate(run,run/'policy.json')
        controls[name]={'rejected':not outcome['passed'],'evaluation_complete':outcome['evaluation_complete']}
        assert not outcome['passed']
        assert outcome['evaluation_complete'] is (name=='wrong_answer')
    save(run/'synthetic_results.json',result)
    manifest['suites']['load']['results_sha256']=digest(run/'synthetic_results.json');save(run/'manifest.json',manifest)
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    summary={'mode':'local_real_adk_synthetic_grades','cases':count,'turns':count*turns,
        'payload_bytes':payload_bytes,'case_concurrency':case_concurrency,
        'peak_active_cases':progress['peak_active_cases'],'delay_per_turn':delay,'generation_seconds':generation_seconds,'gate_seconds':gate_seconds,
        'peak_rss_mib':peak/(1024*1024 if sys.platform=='darwin' else 1024),
        'trace_bytes':(run/'traces.json').stat().st_size,'checkpoint_bytes':(run/'completed_cases.jsonl').stat().st_size,
        'artifact_bytes':sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),
        'fault_controls':controls,'passed':True}
    save(output/'summary.json',summary)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--sizes',type=int,nargs='+',default=[100,500,1000])
    p.add_argument('--turns',type=int,default=3)
    p.add_argument('--payload-bytes',type=int,default=1024)
    p.add_argument('--case-concurrency',type=int,default=1)
    p.add_argument('--delay',type=float,default=0,help='Controlled async fixture delay per turn for overlap measurement.')
    p.add_argument('--worker',action='store_true')
    p.add_argument('--native',action='store_true',help='Run real native grading for one single-turn size (cloud cost).')
    p.add_argument('--qps',type=float,default=2,help='Native grader dispatch rate; not a guarantee of judge quota.')
    p.add_argument('--metric',default='final_response_match_v2',help='Native metric for the single-turn cloud workload.')
    args=p.parse_args();out=args.output.resolve()
    if any(n<1 for n in args.sizes) or args.turns<1 or args.payload_bytes<0 or args.case_concurrency<1 or args.delay<0: p.error('Invalid workload size')
    if args.worker:
        out.mkdir(parents=True,exist_ok=False)
        worker(out,args.sizes[0],args.turns,args.payload_bytes,args.case_concurrency,args.delay);return 0
    out.mkdir(parents=True,exist_ok=False)
    summary={'platform':platform.platform(),'machine':platform.machine(),'python':sys.version,'workloads':[]}
    if args.native:
        if len(args.sizes)!=1: p.error('--native requires exactly one size')
        prepare(out/'pack',args.sizes[0],turns=1,payload_bytes=args.payload_bytes,qps=args.qps,native_metric=args.metric,case_concurrency=args.case_concurrency,delay=args.delay)
        started=time.monotonic()
        with (out/'native.log').open('w') as log:
            code=subprocess.call([sys.executable,'-m','adk_eval_harness','run','--config',str(out/'pack/config.json'),
                                  '--output',str(out/'run')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        gate=read(out/'run/gate.json') if (out/'run/gate.json').exists() else {'passed':False,'scores':[]}
        summary.update(mode='real_adk_google_native_grades',cases=args.sizes[0],native_grades=len(gate['scores']),
                       elapsed_seconds=time.monotonic()-started,exit_code=code,qps=args.qps,requested_metric=args.metric,
                       passed=code==0 and gate['passed'])
    else:
        for size in args.sizes:
            dest=out/str(size)
            with (out/f'{size}.log').open('w') as log:
                subprocess.run([sys.executable,__file__,'--worker','--output',str(dest),'--sizes',str(size),
                                '--turns',str(args.turns),'--payload-bytes',str(args.payload_bytes),
                                '--case-concurrency',str(args.case_concurrency),'--delay',str(args.delay)],
                               check=True,stdout=log,stderr=subprocess.STDOUT,cwd=ROOT)
            summary['workloads'].append(read(dest/'summary.json'))
            print(json.dumps(summary['workloads'][-1]),flush=True)
        summary['passed']=all(w['passed'] for w in summary['workloads'])
    save(out/'summary.json',summary)
    return 0 if summary['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
