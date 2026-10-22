"""Report native scores against declared positive/negative controls; never rewrite scores."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from adk_eval_harness.gate import evaluate, read, trace_checks
from adk_eval_harness.config import symbol


def analyze(run, expected):
    policy=read(run/'policy.json');gate=evaluate(run,run/'policy.json')
    manifest=read(run/'manifest.json');hook=symbol(policy['assertion_hook']) if policy.get('assertion_hook') else lambda c,r:[]
    rows=[]
    for suite,entry in manifest['suites'].items():
        for case in read(run/entry['trace'])['eval_cases']:
            cid=case['eval_case_id'];rules=policy['suites'][suite]['cases'][cid]
            assertions=trace_checks(case,rules)+hook(case,rules)
            scores=[s for s in gate['scores'] if s['suite']==suite and s['case']==cid]
            valid=bool(scores) and all(type(s['score']) in (int,float) for s in scores)
            native_blocking_fail=any(s['score'] < s['threshold'] for s in scores if s['role']=='blocking') if valid else True
            strict_fail=any(s['score'] < s['threshold'] for s in scores) if valid else True
            rows.append({'suite':suite,'case':cid,'expected_behavior':expected,
                         'prospective':suite=='prospective', 'assertions':assertions,
                         'native_scores':scores,'strict_rejects':strict_fail or bool(assertions),
                         'policy_rejects':native_blocking_fail or bool(assertions)})
    return {'evaluation_complete':gate['evaluation_complete'], 'gate_passed':gate['passed'],
            'all_expected_decisions':gate['evaluation_complete'] and bool(rows) and all(
                r['policy_rejects'] == (expected=='faulty') for r in rows), 'cases':rows}


def main():
    p=argparse.ArgumentParser();p.add_argument('runs',type=Path);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();report={'label_origin':'Authored requirements and executed sandbox faults; not independent human labeling or statistical calibration',
                               'runs':{}}
    for name,label in [('finance','correct'),('support','correct'),('finance-faults','faulty'),('support-faults','faulty')]:
        report['runs'][name]=analyze(args.runs/name,label)
    report['passed']=all(r['all_expected_decisions'] for r in report['runs'].values())
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':report['passed'],'runs':{k:{'complete':v['evaluation_complete'],
        'expected_decisions':v['all_expected_decisions'],'cases':len(v['cases'])} for k,v in report['runs'].items()}},indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
