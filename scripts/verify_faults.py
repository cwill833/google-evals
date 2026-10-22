"""Confirm controlled bad executions failed for the intended reasons, not infrastructure."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.quality_gate import evaluate, read

EXPECTED = {
    'wrong_arguments': 'missing expected tool call',
    'skipped_prerequisites': 'wrong sequence',
    'unauthorized_attempt': 'CRITICAL: unauthorized transfer attempt',
    'false_success': 'incorrect JSON response',
}


def verify(run):
    report = evaluate(run, run / 'policy.json')
    manifest = read(run / 'manifest.json')
    errors = []
    if manifest.get('execution_mode') != 'controlled_fault_injection':
        errors.append('Not a controlled fault run')
    if manifest.get('errors') or not manifest.get('commands') or any(
        c['exit_code'] != 0 for c in manifest['commands']
    ):
        errors.append('Native execution did not complete cleanly')
    scores = report['scores']
    if len(scores) != len(EXPECTED) or {s['case'] for s in scores} != set(EXPECTED):
        errors.append('Missing fault grades')
    if any(type(s['score']) not in (int, float) or not 0 <= s['score'] <= 1 for s in scores):
        errors.append('Invalid fault grades')
    for case, expected in EXPECTED.items():
        if not any(e.startswith(f'faults/{case}: {expected}') for e in report['errors']):
            errors.append(f'Expected failure not detected: {case}')
    for error in report['errors']:
        if not any(error.startswith(f'faults/{case}: {expected}') for case, expected in EXPECTED.items()) and not (
            error.startswith('faults/unauthorized_attempt: CRITICAL: forbidden tool')
            or any(error.startswith(f'faults/{case}/final_response_reference_free: ') and ' < ' in error for case in EXPECTED)
        ):
            errors.append(f'Unexpected failure: {error}')
    return {'passed': not errors and not report['passed'], 'errors': errors,
            'detected_faults': list(EXPECTED), 'native_scores': scores}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    result = verify(args.run)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
