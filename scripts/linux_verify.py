"""Run inside a fresh Linux container after extracting the public ZIP into /work.

The caller supplies ADC read-only and project configuration via environment, never the ZIP.
No host virtual environment or package cache is mounted.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
OUT = Path('/verification')


def valid_wrong_reference(manifest, gate):
    """An evaluator/trace error must never count as successful fault detection."""
    scores = gate.get('scores', [])
    if len(scores) != 1:
        return False
    row = scores[0]
    score = row.get('score')
    expected_error = f"{row.get('suite')}/{row.get('case')}/{row.get('metric')}: {score} < 1.0"
    return (
        not manifest.get('errors') and bool(manifest.get('suites'))
        and bool(manifest.get('commands'))
        and all(c.get('exit_code') == 0 for c in manifest['commands'])
        and gate.get('passed') is False
        and row.get('metric') == 'final_response_match'
        and row.get('threshold') == 1.0
        and type(score) in (int, float) and 0 <= score < 1
        and gate.get('errors') == [expected_error]
    )


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'started_at': datetime.now(timezone.utc).isoformat(),
              'platform': platform.platform(), 'machine': platform.machine(),
              'python': sys.version, 'fresh_dependency_cache': True,
              'same_cloud_principal_as_macos': True, 'steps': {}}
    archive = Path('/input/reference.zip')
    report['input_archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    def run(name, argv, expected=0):
        with (OUT / f'{name}.log').open('w') as stream:
            code = subprocess.call(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        report['steps'][name] = {'exit_code': code, 'expected_exit': expected, 'passed': code == expected}
        print(f'{name}: exit {code} (expected {expected})', flush=True)
        (OUT / 'verification.json').write_text(json.dumps(report, indent=2)+'\n')
        return code == expected
    if platform.system() != 'Linux':
        raise RuntimeError('This verifier must run on Linux')
    if not run('install_uv', [sys.executable, '-m', 'pip', 'install', 'uv==0.10.0']):
        return 1
    if not run('locked_install', ['uv', 'sync', '--locked', '--python', sys.executable]):
        return 1
    python = str(ROOT / '.venv/bin/python')
    if not run('unit_tests', [python, '-m', 'pytest', '-q']):
        return 1
    run('release', [python, 'scripts/run_evals.py', '--output', str(OUT/'runs/release')])
    run('faults', [python, 'scripts/run_evals.py', '--fault-injection', '--output', str(OUT/'runs/faults')], 1)
    run('fault_verification', [python, 'scripts/verify_faults.py', str(OUT/'runs/faults')])
    run('wrong_reference', [python, 'scripts/run_evals.py', '--policy', 'tests/eval/policies/negative.json', '--output', str(OUT/'runs/wrong-reference')], 1)
    # A failed invocation alone is not proof that the wrong reference was graded.
    wrong = json.loads((OUT/'runs/wrong-reference/gate.json').read_text())
    manifest = json.loads((OUT/'runs/wrong-reference/manifest.json').read_text())
    report['wrong_reference_valid'] = valid_wrong_reference(manifest, wrong)
    report['passed'] = all(s['passed'] for s in report['steps'].values()) and report['wrong_reference_valid']
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (OUT / 'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
