"""Bounded local/hosted load and native Google grader-only recovery verification."""
import argparse
import json
from pathlib import Path
import platform
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from adk_eval_harness.gate import digest, read
from adk_eval_harness.runner import save
from scripts.load_test import prepare
from scripts.queue_load_test import monitored, pending_pairs
from scripts.linux_verify_hosted import session_evidence, remote_turns, generation_count, gate_summary


def run_native(output, count, concurrency, queue_root, hosted=None):
    output.mkdir(parents=True,exist_ok=False)
    prepare(output/'pack',count,turns=3,payload_bytes=128,qps=.5,
            native_metric='multi_turn_task_success',case_concurrency=concurrency,delay=.02)
    config_path=output/'pack/config.json'
    config=read(config_path)
    config.update(grading_queue=str(queue_root),queue_budget_qps=.5,queue_cooldown_seconds=5,
                  grading_batch_size=20,max_grading_attempts=3,turn_timeout=180)
    if hosted:
        config.update(hosted=hosted,app_name='load')
    save(config_path,config)
    initial_dir=output/'initial';initial_dir.mkdir()
    initial=initial_dir/'run'
    status=monitored([sys.executable,'-m','adk_eval_harness','run','--config',str(config_path),
                      '--output',str(initial)],initial_dir,interrupt_first_grade=True,timeout=3600)
    progress=read(initial/'load/progress.json')
    manifest=read(initial/'manifest.json')
    traces=read(initial/'load/traces.json')['eval_cases']
    preserved={name:digest(initial/name) for name in ('manifest.json','gate.json','report.json','report.html','load/traces.json')}
    evidence=session_evidence(initial)
    original_attempts=len(manifest['suites']['load']['attempts'])
    pending=pending_pairs(initial)
    if not pending:
        raise AssertionError('Grading interruption did not create missing grades')
    recovery_dir=output/'recovery';recovery_dir.mkdir()
    recovered=recovery_dir/'run'
    recovery=monitored([sys.executable,'-m','adk_eval_harness','recover','--config',str(config_path),
                       '--from-run',str(initial),'--output',str(recovered)],recovery_dir,timeout=3600)
    rm=read(recovered/'manifest.json')
    retried=[('load',case['eval_case_id'],attempt['metric'])
             for attempt in rm['suites']['load']['attempts'][original_attempts:]
             for case in read(recovered/attempt['trace'])['eval_cases']]
    sessions=[read(initial/f'load/sessions/load-{i:05d}/state_0.json')['session_id'] for i in range(count)]
    state_ok=all(read(initial/f'load/sessions/load-{i:05d}/state_{t}.json')['state']['user:count']==t+1
                 for i in range(count) for t in range(3))
    checks={
        'bounded_overlap':progress['peak_active_cases']==min(concurrency,count),
        'all_cases_completed':progress['complete'] and progress['completed_cases']==count,
        'unique_sessions':len(set(sessions))==count,
        'isolated_ordered_turn_state':state_ok,
        'stable_trace_order':[c['eval_case_id'] for c in traces]==[f'load-{i:05d}' for i in range(count)],
        'initial_failure_visible':status['exit_code']!=0 and not read(initial/'gate.json')['passed'],
        'interrupted_only_grader':status['injected_interruption'] is not None,
        'original_failure_and_reports_unchanged':all(digest(initial/name)==sha for name,sha in preserved.items()),
        'source_raw_evidence_unchanged':session_evidence(initial)==evidence,
        'recovery_raw_evidence_identical':session_evidence(recovered)==evidence,
        'recovery_trace_identical':digest(recovered/'load/traces.json')==preserved['load/traces.json'],
        'exactly_pending_grades_retried':len(retried)==len(pending) and set(retried)==pending,
        'generation_never_repeated':generation_count(manifest)==generation_count(rm)==1,
        'old_attempts_retained':rm['suites']['load']['attempts'][:original_attempts]==manifest['suites']['load']['attempts'],
        'no_pending_grades':not pending_pairs(recovered),
        'native_gate_passed':recovery['exit_code']==0 and read(recovered/'gate.json')['passed'],
    }
    counts=None
    if hosted:
        counts=remote_turns(initial)
        checks['exact_remote_posts']=counts['remote_run_posts']==count*3 and counts['cases']==count
        checks['no_new_remote_posts']=remote_turns(recovered)==counts
    result={'mode':'hosted' if hosted else 'local','grading':'Google native multi_turn_task_success, unchanged',
            'cases':count,'turns':count*3,'case_concurrency':concurrency,'generation':progress,
            'initial':status,'recovery':recovery,'initial_pending_grades':len(pending),
            'initial_gate':gate_summary(initial),'recovery_gate':gate_summary(recovered),
            'remote_execution':counts,'checks':checks,'passed':all(checks.values())}
    save(output/'summary.json',result)
    print(json.dumps({k:result[k] for k in ('mode','cases','turns','case_concurrency','checks','passed')}),flush=True)
    return result


def run_failure(output, concurrency, hosted=None):
    """A real agent failure must preserve partial work and block grader-only recovery."""
    output.mkdir(parents=True,exist_ok=False)
    prepare(output/'pack',20,turns=3,payload_bytes=32,case_concurrency=concurrency,delay=.1)
    config_path=output/'pack/config.json';config=read(config_path)
    config['turn_timeout']=180
    if hosted:
        config.update(hosted=hosted,app_name='load')
    save(config_path,config)
    dataset=read(output/'pack/dataset.json')
    msg=json.loads(dataset[0]['messages'][0]);msg.update(fail=True,delay=.01)
    dataset[0]['messages'][0]=json.dumps(msg)
    # Keep duplicated prompt representation consistent with validation.
    dataset[0]['prompt']['parts'][0]['text']=dataset[0]['messages'][0]
    save(output/'pack/dataset.json',dataset)
    first=output/'initial';first.mkdir()
    status=monitored([sys.executable,'-m','adk_eval_harness','run','--config',str(config_path),
                      '--output',str(first/'run')],first,timeout=900)
    run=first/'run';progress=read(run/'load/progress.json')
    manifest=read(run/'manifest.json');raw=session_evidence(run)
    second=output/'recovery';second.mkdir()
    retry=monitored([sys.executable,'-m','adk_eval_harness','recover','--config',str(config_path),
                     '--from-run',str(run),'--output',str(second/'run')],second,timeout=120)
    failed=read(run/'load/sessions/load-00000/turn_0.json')
    checks={'generation_failed':status['exit_code']!=0 and not manifest['generation_complete'],
            'not_graded':not any(s.get('attempts') for s in manifest['suites'].values()),
            'partial_failure_visible':failed['telemetry']['status']=='error' and not read(run/'gate.json')['passed'],
            'stopped_dispatch':any(v=='not_started' for v in progress['case_statuses'].values()),
            'drained_inflight':not any(v=='running' for v in progress['case_statuses'].values()),
            'failed_case_not_retried':len(list((run/'load/sessions/load-00000').glob('turn_*.json')))==1,
            'recovery_refused':retry['exit_code']!=0,
            'raw_evidence_unchanged':session_evidence(run)==raw}
    if hosted:
        checks['failed_remote_post_not_retried']=failed['transport_stats_after_turn']['run_posts']==1
    result={'mode':'hosted' if hosted else 'local','progress':progress,'checks':checks,'passed':all(checks.values())}
    save(output/'summary.json',result)
    print(json.dumps({'failure_scenario':result['mode'],'checks':checks,'passed':result['passed']}),flush=True)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--queue-root',type=Path,required=True)
    p.add_argument('--endpoint',type=Path,required=True)
    p.add_argument('--deployment-manifest',type=Path,required=True)
    args=p.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    hosted={'url':read(args.endpoint)['url'],'auth':'gcloud','deployment_manifest':str(args.deployment_manifest.resolve())}
    report={'platform':platform.platform(),'steps':{}}
    for name,operation in [
        ('local_failure',lambda:run_failure(out/'local-failure',4)),
        ('hosted_failure',lambda:run_failure(out/'hosted-failure',4,hosted)),
        ('local_native',lambda:run_native(out/'local-native',24,4,args.queue_root/'local')),
        ('hosted_native',lambda:run_native(out/'hosted-native',100,8,args.queue_root/'hosted',hosted)),
    ]:
        try:
            report['steps'][name]=operation()
        except Exception as exc:
            report['steps'][name]={'passed':False,'error_class':type(exc).__name__,'error':str(exc)}
            print(name+': failed; evidence preserved',flush=True)
        save(out/'verification.json',report)
    report['passed']=all(step['passed'] for step in report['steps'].values())
    save(out/'verification.json',report)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
