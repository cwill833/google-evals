"""Fresh Linux/public-package concurrency and native recovery checks; incurs Google grading cost."""
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.linux_verify_hosted import package_check, execute, save, OUT, INPUT


def main():
    if platform.system()!='Linux' or (ROOT/'.venv').exists() or (OUT/'verification.json').exists():
        raise ValueError('Clean Linux package and fresh output required')
    OUT.mkdir(parents=True,exist_ok=True)
    report={'platform':platform.platform(),'machine':platform.machine(),**package_check(),'steps':{}}
    python=str(ROOT/'.venv/bin/python')
    commands=[
        ('install_uv',[sys.executable,'-m','pip','install','uv==0.10.0']),
        ('locked_install',['uv','sync','--locked','--python',sys.executable]),
        ('unit_tests',[python,'-m','pytest','-q']),
        ('sequential_baseline',[python,'scripts/load_test.py','--output',str(OUT/'baseline'),
                                '--sizes','100','--delay','.02']),
        ('concurrent_capacity',[python,'scripts/load_test.py','--output',str(OUT/'capacity'),
                                '--sizes','100','500','1000','--case-concurrency','8','--delay','.02']),
        ('native_and_failures',[python,'scripts/concurrency_load_test.py','--output',str(OUT/'live'),
                               '--queue-root',str(ROOT/'artifacts/concurrency-queues'),
                               '--endpoint',str(INPUT/'endpoint.json'),
                               '--deployment-manifest',str(INPUT/'deployment-manifest.json')]),
    ]
    for name,command in commands:
        report['steps'][name]=execute(name,command,timeout=10800)
        save(OUT/'verification.json',report)
        if not report['steps'][name]['passed']:
            break
    report['passed']=len(report['steps'])==len(commands) and all(s['passed'] for s in report['steps'].values())
    save(OUT/'verification.json',report)
    print(json.dumps(report),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
