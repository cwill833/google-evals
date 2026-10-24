"""Clean Linux package validation. Cloud load is opt-in via --native-cases."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
OUT=Path('/verification')


def main():
    p=argparse.ArgumentParser();p.add_argument('--native-cases',type=int,default=0)
    p.add_argument('--support',action='store_true')
    p.add_argument('--historical-runs',type=Path,help='Optional external finance/support native runs directory.')
    p.add_argument('--native-qps',type=float,default=2)
    args=p.parse_args()
    if platform.system()!='Linux': raise RuntimeError('Linux required')
    OUT.mkdir(parents=True,exist_ok=True)
    report={'started_at':datetime.now(timezone.utc).isoformat(),'platform':platform.platform(),
        'machine':platform.machine(),'python':sys.version,'fresh_dependency_cache':True,
        'input_archive_sha256':hashlib.sha256(Path('/input/reference.zip').read_bytes()).hexdigest(),'steps':{}}
    def execute(name,argv):
        with (OUT/f'{name}.log').open('w') as log:
            code=subprocess.call(argv,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        report['steps'][name]={'exit_code':code,'passed':code==0}
        (OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
        print(f'{name}: exit {code}',flush=True)
        return code==0
    for name,argv in [('install_uv',[sys.executable,'-m','pip','install','uv==0.10.0']),
                      ('locked_install',['uv','sync','--locked','--python',sys.executable])]:
        if not execute(name,argv): return 1
    python=str(ROOT/'.venv/bin/python')
    if not execute('unit_tests',[python,'-m','pytest','-q']): return 1
    if not execute('local_load',[python,'scripts/load_test.py','--output',str(OUT/'local'),
                                 '--sizes','100','500','1000']): return 1
    if args.native_cases:
        execute('native_load',[python,'scripts/load_test.py','--native','--sizes',str(args.native_cases),
                              '--payload-bytes','128','--qps',str(args.native_qps),'--output',str(OUT/'native')])
    if args.support:
        execute('support',[python,'-m','adk_eval_harness','run','--config','packs/support/config.json',
                           '--output',str(OUT/'support')])
    if args.historical_runs is not None:
        execute('historical_controls',[python,'scripts/calibrate_policy.py',
                str(args.historical_runs.resolve()),'--output',str(OUT/'historical-controls.json')])
    report['passed']=all(s['passed'] for s in report['steps'].values())
    report['finished_at']=datetime.now(timezone.utc).isoformat()
    (OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
