"""Clean Linux package reproduction of both ADK agents and their executed negative controls."""
import concurrent.futures
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import sys
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
OUT=Path('/verification')


def main():
    if platform.system()!='Linux': raise RuntimeError('Linux required')
    OUT.mkdir(parents=True,exist_ok=True)
    report={'started_at':datetime.now(timezone.utc).isoformat(),'platform':platform.platform(),
            'machine':platform.machine(),'python':sys.version,'fresh_dependency_cache':True,
            'same_cloud_principal_as_macos':True,
            'input_archive_sha256':hashlib.sha256(Path('/input/reference.zip').read_bytes()).hexdigest(),'steps':{}}
    def execute(name,argv,expected=0):
        with (OUT/f'{name}.log').open('w') as f:
            code=subprocess.call(argv,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
        print(f'{name}: exit {code}; expected {expected}',flush=True)
        return name,{'exit_code':code,'expected_exit':expected,'passed':code==expected}
    def record(result):
        name,value=result;report['steps'][name]=value
        (OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
        return value['passed']
    for name,argv in [('install_uv',[sys.executable,'-m','pip','install','uv==0.10.0']),
                      ('locked_install',['uv','sync','--locked','--python',sys.executable]),
                      ('unit_tests',[str(ROOT/'.venv/bin/python'),'-m','pytest','-q'])]:
        if not record(execute(name,argv)): return 1
    python=str(ROOT/'.venv/bin/python')
    # Separate OS processes isolate imports, ADK runtimes and domain state; max two evaluations concurrently.
    jobs=[('finance','packs/finance/config.json',0),('support','packs/support/config.json',0),
          ('finance-faults','packs/finance/fault-config.json',1),('support-faults','packs/support/fault-config.json',1)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(execute,name,[python,'-m','adk_eval_harness','run','--config',config,'--output',str(OUT/'runs'/name)],expected)
                 for name,config,expected in jobs]
        for future in concurrent.futures.as_completed(futures): record(future.result())
    record(execute('calibration',[python,'scripts/calibrate_policy.py',str(OUT/'runs'),'--output',str(OUT/'calibration.json')]))
    report['passed']=all(s['passed'] for s in report['steps'].values())
    report['finished_at']=datetime.now(timezone.utc).isoformat()
    (OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
