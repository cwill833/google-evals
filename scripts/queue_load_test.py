"""One-host queue/recovery capacity checks; hosted grading is explicit opt-in.

Local contention is real subprocess execution. Recovery contract tests use
clearly synthetic grades. --native-cases runs actual Google native grading,
interrupts the first grading batch, and explicitly recovers unavailable pairs.
Queue storage must support local POSIX file locks and SQLite locking; use
--queue-root on the native container filesystem instead of a Docker Desktop
host bind mount. Evidence output may remain on the host bind mount.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def queue_worker(output, index, units, budget, queue_root=None):
    from adk_eval_harness.grading_queue import GradingQueue
    queue = GradingQueue(queue_root or output / 'shared-queue', budget_qps=budget, wait_timeout=60, cooldown_seconds=0)
    (output / f'ready-{index}').touch()
    deadline = time.monotonic() + 30
    while not (output / 'start').exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('Contention start barrier timed out')
        time.sleep(.01)
    with queue.acquire(units=units, requested_qps=budget * 2) as lease:
        entered = time.time()
        time.sleep(.02)
        exited = time.time()
    save(output / f'worker-{index}.json', {'worker': index, 'entered': entered, 'exited': exited,
                                         'lease': lease.record})


def contention(output, workers, units=2, budget=50, queue_root=None):
    queue_path = Path(queue_root or output / 'shared-queue').resolve()
    output.mkdir(parents=True, exist_ok=False)
    processes, logs = [], []
    started = time.monotonic()
    try:
        for index in range(workers):
            log = (output / f'worker-{index}.log').open('w')
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, __file__, '--queue-worker', str(index),
                '--output', str(output), '--units', str(units), '--queue-budget', str(budget),
                '--queue-root', str(queue_path)],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT))
        deadline = time.monotonic() + 30
        while len(list(output.glob('ready-*'))) != workers:
            if any(process.poll() not in (None, 0) for process in processes):
                raise RuntimeError('Queue worker exited before start barrier; see worker logs')
            if time.monotonic() >= deadline:
                raise TimeoutError('Queue worker startup timed out')
            time.sleep(.02)
        (output / 'start').touch()
        for process in processes:
            if process.wait(timeout=60) != 0:
                raise RuntimeError('Queue worker failed; see worker logs')
        reports = [read(output / f'worker-{index}.json') for index in range(workers)]
        by_start = sorted(reports, key=lambda r: r['lease']['acquired_at'])
        with sqlite3.connect(queue_path / 'queue.sqlite3') as connection:
            fifo = [row[0] for row in connection.execute('SELECT ticket FROM tickets ORDER BY sequence')]
        checks = {
            'all_workers_completed': all(r['lease']['status'] == 'completed' for r in reports),
            'fifo_order': fifo == [r['lease']['ticket'] for r in by_start],
            'requested_qps_capped': all(r['lease']['effective_qps'] == budget for r in reports),
            'no_overlapping_leases': all(b['entered'] >= a['exited'] for a, b in zip(by_start, by_start[1:])),
            'dispatch_budget_spacing': all(b['lease']['acquired_at'] + .002 >= a['lease']['acquired_at'] + units / budget
                                          for a, b in zip(by_start, by_start[1:])),
        }
        summary = {'mode': 'local_real_subprocess_queue_contention_no_google_calls',
                   'workers': workers, 'units_per_worker': units, 'budget_qps': budget,
                   'queue_path': str(queue_path),
                   'elapsed_seconds': time.monotonic() - started, 'checks': checks,
                   'max_wait_seconds': max(r['lease']['wait_seconds'] for r in reports),
                   'passed': all(checks.values())}
        save(output / 'summary.json', summary)
        return summary
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
        for log in logs:
            log.close()


def grade_attempts(run):
    try:
        manifest = read(run / 'manifest.json')
    except (OSError, ValueError):
        return []
    return [attempt for suite in manifest.get('suites', {}).values() for attempt in suite.get('attempts', [])]


def monitored(argv, output, *, interrupt_first_grade=False, timeout=1800):
    """Bounded process run; the injected signal targets only a recorded native grader."""
    run = output / 'run'
    signaled = None
    started = time.monotonic()
    with (output / 'command.log').open('w') as log:
        process = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                if interrupt_first_grade and signaled is None:
                    for attempt in grade_attempts(run):
                        command = attempt.get('command') or {}
                        pid = command.get('pid')
                        if pid and command.get('exit_code') is None:
                            try:
                                os.killpg(pid, signal.SIGTERM)
                                signaled = {'pid': pid, 'metric': attempt.get('metric'),
                                            'trace': attempt.get('trace'), 'at': time.time()}
                            except ProcessLookupError:
                                continue
                            break
                if time.monotonic() - started >= timeout:
                    raise TimeoutError('Native verification exceeded its total time budget')
                time.sleep(.05)
        finally:
            if process.poll() is None:
                # Native graders have their own process groups; clean them before the parent.
                for attempt in grade_attempts(run):
                    command = attempt.get('command') or {}
                    if command.get('pid') and command.get('exit_code') is None:
                        try:
                            os.killpg(command['pid'], signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    return {'exit_code': process.returncode, 'elapsed_seconds': time.monotonic() - started,
            'injected_interruption': signaled}


def pending_pairs(run):
    from adk_eval_harness.attempts import resolve
    manifest, policy = read(run / 'manifest.json'), read(run / 'policy.json')
    return {(suite, cid, metric) for suite, entry in manifest['suites'].items()
            for cid, metric in resolve(run, suite, entry, policy['suites'][suite])[2]}


def native_recovery(output, count, qps, timeout, queue_root=None):
    from load_test import prepare
    from adk_eval_harness.gate import digest
    output.mkdir(parents=True, exist_ok=False)
    pack = output / 'pack'
    prepare(pack, count, turns=1, payload_bytes=128, qps=qps, native_metric='final_response_match_v2')
    config_path = pack / 'config.json'
    config = read(config_path)
    config.update(grading_queue=str(Path(queue_root or output / 'shared-queue').resolve()), queue_budget_qps=qps,
                  queue_cooldown_seconds=30, grading_batch_size=100, max_grading_attempts=3)
    save(config_path, config)
    first = output / 'initial'
    first.mkdir()
    first_report = monitored([sys.executable, '-m', 'adk_eval_harness', 'run', '--config', str(config_path),
                              '--output', str(first / 'run')], first, interrupt_first_grade=True, timeout=timeout)
    initial_run = first / 'run'
    if not (initial_run / 'manifest.json').is_file():
        raise RuntimeError('Initial native run did not persist a manifest')
    initial_manifest = read(initial_run / 'manifest.json')
    initial_trace_hash = digest(initial_run / 'load/traces.json')
    initial_manifest_hash = digest(initial_run / 'manifest.json')
    pending_set = pending_pairs(initial_run)
    pending = len(pending_set)
    summary = {'mode': 'real_adk_google_native_grades_with_injected_grading_interruption',
               'cases': count, 'qps': qps, 'initial': first_report, 'initial_pending_pairs': pending,
               'initial_generation_complete': initial_manifest.get('generation_complete')}
    recovery = output / 'recovery'
    recovery.mkdir()
    if pending:
        recovery_report = monitored([sys.executable, '-m', 'adk_eval_harness', 'recover', '--config', str(config_path),
                    '--from-run', str(initial_run), '--output', str(recovery / 'run')], recovery, timeout=timeout)
        summary['recovery'] = recovery_report
        recovered_run = recovery / 'run'
        gate = read(recovered_run / 'gate.json')
        recovered_manifest = read(recovered_run / 'manifest.json')
        original_attempts = len(initial_manifest['suites']['load']['attempts'])
        new_attempts = recovered_manifest['suites']['load']['attempts'][original_attempts:]
        recovery_pairs = [('load', case['eval_case_id'], attempt['metric']) for attempt in new_attempts
                          for case in read(recovered_run / attempt['trace'])['eval_cases']]
        def generation_count(manifest):
            return sum('generate' in command.get('argv', []) for command in manifest.get('commands', []))
        checks = {'interrupted_only_native_grade': first_report['injected_interruption'] is not None,
                  'all_cases_generated_before_grading': initial_manifest.get('generation_complete') is True,
                  'source_manifest_unchanged': digest(initial_run / 'manifest.json') == initial_manifest_hash,
                  'original_trace_unchanged': digest(initial_run / 'load/traces.json') == initial_trace_hash,
                  'recovery_trace_identical': digest(recovered_run / 'load/traces.json') == initial_trace_hash,
                  'recovery_requested_exactly_pending_pairs': len(recovery_pairs) == pending and set(recovery_pairs) == pending_set,
                  'agent_generation_not_repeated': generation_count(initial_manifest) == 1 and generation_count(recovered_manifest) == 1,
                  'recovery_completed_native_coverage': gate['evaluation_complete'],
                  'recovery_release_gate_passed': gate['passed']}
        summary.update(checks=checks, recovered_native_grades=len(gate['scores']),
                       remaining_pending_pairs=len(pending_pairs(recovered_run)), passed=all(checks.values()))
    else:
        summary.update(passed=False, error='No pending grades; interruption scenario was not exercised')
    save(output / 'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--units', type=int, default=2)
    parser.add_argument('--queue-budget', type=float, default=50)
    parser.add_argument('--queue-worker', type=int)
    parser.add_argument('--queue-root', type=Path, help='Local POSIX/SQLite queue storage root; separate from evidence output, especially in containers.')
    parser.add_argument('--native-cases', type=int, default=0, help='Opt in to hosted metric cost; use 200 for two batches.')
    parser.add_argument('--native-qps', type=float, default=.5)
    parser.add_argument('--native-timeout', type=float, default=1800)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.queue_worker is not None:
        queue_worker(output, args.queue_worker, args.units, args.queue_budget, args.queue_root)
        return 0
    if args.workers < 2 or args.units < 1 or args.queue_budget <= 0 or args.native_cases < 0 or args.native_qps <= 0:
        parser.error('Positive workload sizes/rates required; use at least two queue workers')
    output.mkdir(parents=True, exist_ok=False)
    report = {'platform': platform.platform(), 'machine': platform.machine(), 'python': sys.version, 'steps': {}}
    try:
        report['steps']['contention'] = contention(output / 'contention', args.workers, args.units, args.queue_budget,
                                                    args.queue_root / 'contention' if args.queue_root else None)
        save(output / 'verification.json', report)
        with (output / 'recovery-contract-tests.log').open('w') as log:
            code = subprocess.call([sys.executable, '-m', 'pytest', '-q', 'tests/test_recovery_contract.py'],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        report['steps']['recovery_contract'] = {'mode': 'synthetic_native_shaped_test_fixtures',
                                                'exit_code': code, 'passed': code == 0}
        save(output / 'verification.json', report)
        if args.native_cases:
            report['steps']['native'] = native_recovery(output / 'native', args.native_cases,
                                                         args.native_qps, args.native_timeout,
                                                         args.queue_root / 'native' if args.queue_root else None)
        report['passed'] = all(step['passed'] for step in report['steps'].values())
    except Exception as exc:
        report.update(passed=False, error=f'{type(exc).__name__}: {exc}')
    save(output / 'verification.json', report)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
