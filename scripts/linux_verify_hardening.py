"""Clean Linux public-package regression verification; no cloud credentials or grading.

Install the locked environment, test the review fixes, load real local ADK sessions,
and optionally replay separately supplied native evidence without new cloud calls.
"""
import argparse
import json
from pathlib import Path
import platform
import sys
import tempfile
import shutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.linux_verify_hosted import package_check, execute, save, OUT


def replay(evidence):
    from adk_eval_harness.gate import evaluate, digest
    from adk_eval_harness.reporting import build_report
    evidence = Path(evidence).resolve()
    results = {}
    for name, passed, grades, failures in [
        ('local-initial', False, 4, 0), ('local-recovery', True, 24, 20),
        ('hosted-initial', False, 80, 0), ('hosted-recovery', True, 100, 20),
    ]:
        run = evidence / name
        before = {str(p.relative_to(run)): digest(p) for p in run.rglob('*') if p.is_file()}
        gate = evaluate(run, run / 'policy.json')
        report = build_report(run)
        assert gate['passed'] is report['passed'] is passed
        assert report['native_grade_count'] == grades
        assert gate['recovered_grading_failures'] == failures
        assert not report['telemetry_integrity']['errors']
        assert all(digest(run / name) == sha for name, sha in before.items())
        results[name] = {'gate_passed': passed, 'native_grades': grades,
                         'recovered_failures_retained': failures, 'files_unchanged': len(before),
                         'telemetry_integrity_errors': 0}
    with tempfile.TemporaryDirectory() as temporary:
        run = Path(temporary) / 'tampered'
        shutil.copytree(evidence / 'local-recovery', run)
        state = next(run.glob('*/sessions/*/state_0.json'))
        state.write_text(state.read_text() + '\n')
        gate, report = evaluate(run, run / 'policy.json'), build_report(run)
        assert gate['passed'] is report['passed'] is False
        assert gate['evaluation_complete'] is report['evaluation_complete'] is False
    save(OUT / 'native-evidence-replay.json', {'passed': True, 'runs': results,
         'tampered_state_rejected_by_gate_and_report': True,
         'new_agent_calls': 0, 'new_native_grading_calls': 0,
         'scope': 'Offline replay of previously captured native scores; not fresh cloud grading.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', action='store_true', help='Only replay externally supplied native evidence.')
    parser.add_argument('--evidence-root', type=Path, help='Local concurrency evidence directory; never bundled.')
    args = parser.parse_args()
    if args.replay and args.evidence_root is None:
        parser.error('--replay requires --evidence-root; run evidence is kept outside the source package')
    if args.evidence_root is not None:
        for name in ('local-initial', 'local-recovery', 'hosted-initial', 'hosted-recovery'):
            if not (args.evidence_root / name / 'manifest.json').is_file():
                parser.error('Missing external evidence manifest: ' + str(args.evidence_root / name))
    if args.replay:
        replay(args.evidence_root)
        return 0
    if platform.system() != 'Linux' or (ROOT / '.venv').exists() or (OUT / 'verification.json').exists():
        raise ValueError('Clean Linux package and fresh output required')
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'platform': platform.platform(), 'machine': platform.machine(),
              **package_check(), 'scope': 'Offline regression and local ADK capacity; optional external native replay',
              'native_replay_requested': args.evidence_root is not None,
              'cloud_calls': 0, 'steps': {}}
    python = str(ROOT / '.venv/bin/python')
    capacity = Path('/work/hardening-capacity')
    commands = [
        ('install_uv', [sys.executable, '-m', 'pip', 'install', 'uv==0.10.0']),
        ('locked_install', ['uv', 'sync', '--locked', '--python', sys.executable]),
        ('unit_tests', [python, '-m', 'pytest', '-q']),
        ('concurrent_capacity', [python, 'scripts/load_test.py', '--output', str(capacity),
                                 '--sizes', '100', '1000', '--case-concurrency', '8', '--delay', '.02']),
    ]
    if args.evidence_root is not None:
        commands.append(('native_evidence_replay', [python, 'scripts/linux_verify_hardening.py', '--replay',
                                                    '--evidence-root', str(args.evidence_root.resolve())]))
    for name, command in commands:
        report['steps'][name] = execute(name, command, timeout=1800)
        save(OUT / 'verification.json', report)
        if not report['steps'][name]['passed']:
            break
    report['passed'] = len(report['steps']) == len(commands) and all(s['passed'] for s in report['steps'].values())
    if (capacity / 'summary.json').is_file():
        shutil.copyfile(capacity / 'summary.json', OUT / 'capacity-summary.json')
    save(OUT / 'verification.json', report)
    print(json.dumps(report), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
