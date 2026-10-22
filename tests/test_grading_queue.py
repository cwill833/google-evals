"""Real POSIX processes exercise FIFO, shared budgets and crash-safe exclusion."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from adk_eval_harness.grading_queue import GradingQueue


WORKER = '''
import json, sys, time
from adk_eval_harness.grading_queue import GradingQueue
with GradingQueue(sys.argv[1], budget_qps=20, wait_timeout=10).acquire(1,100) as lease:
    time.sleep(.05)
print(json.dumps(lease.record), flush=True)
'''


def tickets(path):
    with sqlite3.connect(path / 'queue.sqlite3') as db:
        return db.execute('SELECT ticket,status,acquired_at,finished_at FROM tickets ORDER BY sequence').fetchall()


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for subprocess state')


def worker(path, code=WORKER):
    return subprocess.Popen([sys.executable, '-c', code, str(path)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def finish(process):
    out, err = process.communicate(timeout=10)
    assert process.returncode == 0, err
    return json.loads(out)


def test_fifo_and_qps_cap_across_independent_processes(tmp_path):
    queue = GradingQueue(tmp_path, budget_qps=20)
    children = []
    try:
        with queue.acquire(1,100):
            for count in (2,3,4):
                children.append(worker(tmp_path))
                wait_for(lambda: len(tickets(tmp_path)) == count)
        records = [finish(p) for p in children]
        assert all(r['effective_qps'] == 20 and r['requested_qps'] == 100 for r in records)
        assert [r['acquired_at'] for r in records] == sorted(r['acquired_at'] for r in records)
        assert all(b['acquired_at'] >= a['finished_at'] for a,b in zip(records,records[1:]))
        assert [row[1] for row in tickets(tmp_path)] == ['completed'] * 4
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
                process.wait()


def test_reservations_and_cooldown_persist_between_instances(tmp_path):
    queue = GradingQueue(tmp_path, budget_qps=20, cooldown_seconds=.15)
    with queue.acquire(4,100) as first:
        first.throttled()
    with GradingQueue(tmp_path, budget_qps=20).acquire(1,10) as second:
        assert second.qps == 10
        assert second.record['acquired_at'] >= first.record['next_eligible_at']
    assert first.record['throttle_count'] == 1
    assert first.record['next_eligible_at'] >= first.record['finished_at'] + .15
    with pytest.raises(RuntimeError, match='no longer active'):
        first.throttled()


def test_dead_waiter_is_reaped_without_lease_expiry(tmp_path):
    queue = GradingQueue(tmp_path, budget_qps=20)
    child = None
    try:
        with queue.acquire(1,20):
            child = worker(tmp_path)
            wait_for(lambda: len(tickets(tmp_path)) == 2)
            child.kill()
            child.wait()
        with queue.acquire(1,20):
            pass
        assert [r[1] for r in tickets(tmp_path)] == ['completed','abandoned','completed']
    finally:
        if child and child.poll() is None:
            child.kill()
            child.wait()


def test_timeout_preserves_ticket_and_does_not_steal_live_lock(tmp_path):
    queue = GradingQueue(tmp_path, budget_qps=20)
    with queue.acquire(1,20):
        with pytest.raises(TimeoutError):
            with GradingQueue(tmp_path, budget_qps=20, wait_timeout=.05).acquire(1,20):
                pytest.fail('A live lease must not be stolen')
    assert [r[1] for r in tickets(tmp_path)] == ['completed','timed_out']


def test_mixed_budget_is_rejected_and_invalid_requests_never_enqueue(tmp_path):
    queue = GradingQueue(tmp_path, budget_qps=20)
    with pytest.raises(ValueError, match='immutable budget'):
        GradingQueue(tmp_path, budget_qps=10)
    for units, qps in [(0,1),(True,1),(1,float('nan')),(1,0)]:
        with pytest.raises(ValueError):
            with queue.acquire(units,qps):
                pass
    assert tickets(tmp_path) == []


def test_native_child_inherits_lock_after_parent_death(tmp_path):
    # The lease owner dies without cleanup; an independent native child retains
    # the dispatch fd. No process may dispatch until that child exits.
    queue = GradingQueue(tmp_path, budget_qps=20)
    code = '''
import os, subprocess, sys
from adk_eval_harness.grading_queue import GradingQueue
with GradingQueue(sys.argv[1], budget_qps=20).acquire(1,100) as lease:
    subprocess.Popen([sys.executable,'-c','import time; time.sleep(.7)'],
                     pass_fds=lease.pass_fds, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('ready',flush=True)
    os._exit(0)
'''
    owner = worker(tmp_path, code)
    out, err = owner.communicate(timeout=5)
    assert owner.returncode == 0 and out.strip() == 'ready', err
    started = time.monotonic()
    with queue.acquire(1,20):
        assert time.monotonic() - started >= .5
    assert [r[1] for r in tickets(tmp_path)] == ['abandoned','completed']


def test_failed_lease_keeps_reservation_and_records_failure(tmp_path):
    queue = GradingQueue(tmp_path, budget_qps=20)
    with pytest.raises(RuntimeError, match='native failure'):
        with queue.acquire(3,20) as failed:
            raise RuntimeError('native failure')
    with queue.acquire(1,20) as next_lease:
        assert next_lease.record['acquired_at'] >= failed.record['next_eligible_at']
    assert failed.record['status'] == 'failed'
