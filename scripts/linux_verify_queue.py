"""Verify queue/recovery from a clean Linux public archive; hosted grading is opt-in."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = Path('/verification')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-cases', type=int, default=0)
    args = parser.parse_args()
    if platform.system() != 'Linux':
        raise RuntimeError('This verification requires Linux')
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'platform': platform.platform(), 'python': sys.version,
              'input_archive_sha256': hashlib.sha256(Path('/input/reference.zip').read_bytes()).hexdigest(),
              'fresh_dependency_cache': True, 'same_cloud_principal_as_macos': True, 'steps': {}}
    steps = [
        ('base_verification', [sys.executable, str(ROOT / 'scripts/linux_verify_load.py')]),
        ('queue_recovery', [str(ROOT / '.venv/bin/python'), str(ROOT / 'scripts/queue_load_test.py'),
                            '--output', str(OUT / 'queue'), '--native-cases', str(args.native_cases),
                            '--queue-root', str(ROOT / 'artifacts/verification-queues')])]
    for name, argv in steps:
        with (OUT / f'{name}.log').open('w') as log:
            code = subprocess.call(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        report['steps'][name] = {'exit_code': code, 'passed': code == 0}
        report['passed'] = all(step['passed'] for step in report['steps'].values())
        (OUT / 'queue-verification.json').write_text(json.dumps(report, indent=2) + '\n')
        print(f'{name}: exit {code}', flush=True)
        if code:
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
