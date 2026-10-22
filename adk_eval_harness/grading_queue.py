"""One-host FIFO admission and dispatch budgeting for native grading processes.

All cooperating processes must use the same directory on a local POSIX filesystem.
This is not a distributed limiter or a guarantee about Google's internal judge calls.
Pass lease.pass_fds to the native child; its inherited lock survives parent death.
"""
from contextlib import contextmanager
import fcntl
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid


def _positive(value, name, *, zero=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ValueError(f'{name} must be finite and {"nonnegative" if zero else "positive"}')
    return float(value)


def _lock_file(path):
    return os.open(path, os.O_CREAT | os.O_RDWR, 0o600)


def _try_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


class Lease:
    def __init__(self, queue, ticket, fd, record):
        self._queue, self._ticket = queue, ticket
        self.qps = record['effective_qps']
        self.pass_fds = (fd,)
        self.record = record
        self._active = True

    def throttled(self):
        """Record observed service throttling and persist a cooldown immediately."""
        if not self._active:
            raise RuntimeError('Grading lease is no longer active')
        now = time.time()
        with self._queue._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE budget SET next_eligible = MAX(next_eligible, ?) WHERE id=1',
                       (now + self._queue.cooldown_seconds,))
            db.execute('UPDATE tickets SET throttle_count=throttle_count+1 WHERE ticket=?', (self._ticket,))
            self.record['throttle_count'] += 1
            self.record['next_eligible_at'] = db.execute('SELECT next_eligible FROM budget WHERE id=1').fetchone()[0]


class GradingQueue:
    def __init__(self, path, budget_qps=.5, wait_timeout=3600, cooldown_seconds=30):
        self.path = Path(path).resolve()
        self.budget_qps = _positive(budget_qps, 'budget_qps')
        self.wait_timeout = _positive(wait_timeout, 'wait_timeout', zero=True)
        self.cooldown_seconds = _positive(cooldown_seconds, 'cooldown_seconds', zero=True)
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK(id=1), qps REAL NOT NULL, next_eligible REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS tickets (sequence INTEGER PRIMARY KEY AUTOINCREMENT, ticket TEXT UNIQUE NOT NULL, status TEXT NOT NULL, units INTEGER NOT NULL, requested_qps REAL NOT NULL, effective_qps REAL NOT NULL, queued_at REAL NOT NULL, acquired_at REAL, finished_at REAL, throttle_count INTEGER NOT NULL DEFAULT 0)')
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO budget VALUES (1, ?, 0)', (self.budget_qps,))
            if db.execute('SELECT qps FROM budget WHERE id=1').fetchone()[0] != self.budget_qps:
                raise ValueError('Shared queue budget_qps differs from the existing immutable budget')

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path / 'queue.sqlite3', timeout=5)
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _reap(self, db, own_ticket):
        # Called only while holding the global dispatch lock. A still-running child
        # inherited that lock, so its dead parent's ticket cannot be reaped early.
        for ticket, in db.execute("SELECT ticket FROM tickets WHERE status IN ('waiting','running') AND ticket != ?", (own_ticket,)).fetchall():
            fd = _lock_file(self.path / (ticket + '.lock'))
            try:
                if _try_lock(fd):
                    db.execute("UPDATE tickets SET status='abandoned', finished_at=? WHERE ticket=?", (time.time(), ticket))
            finally:
                os.close(fd)

    @contextmanager
    def acquire(self, units, requested_qps):
        if type(units) is not int or units < 1:
            raise ValueError('units must be a positive integer count of case-metric computations')
        requested_qps = _positive(requested_qps, 'requested_qps')
        effective_qps = min(requested_qps, self.budget_qps)
        ticket = uuid.uuid4().hex
        ticket_fd = _lock_file(self.path / (ticket + '.lock'))
        global_fd = None
        lease = None
        status = 'interrupted'
        registered = False
        started = time.monotonic()
        record = {'ticket': ticket, 'queue_path': str(self.path), 'status': 'waiting',
                  'units': units, 'requested_qps': requested_qps, 'effective_qps': effective_qps,
                  'budget_qps': self.budget_qps, 'queued_at': time.time(), 'throttle_count': 0}
        try:
            fcntl.flock(ticket_fd, fcntl.LOCK_EX)
            with self._db() as db:
                db.execute('INSERT INTO tickets(ticket,status,units,requested_qps,effective_qps,queued_at) VALUES (?,?,?,?,?,?)',
                           (ticket, 'waiting', units, requested_qps, effective_qps, record['queued_at']))
            registered = True
            global_fd = _lock_file(self.path / 'dispatch.lock')
            while True:
                acquired = False
                if _try_lock(global_fd):
                    try:
                        with self._db() as db:
                            db.execute('BEGIN IMMEDIATE')
                            self._reap(db, ticket)
                            first = db.execute("SELECT ticket FROM tickets WHERE status IN ('waiting','running') ORDER BY sequence LIMIT 1").fetchone()
                            now = time.time()
                            eligible = db.execute('SELECT next_eligible FROM budget WHERE id=1').fetchone()[0]
                            if first and first[0] == ticket and now >= eligible:
                                next_eligible = now + units / effective_qps
                                db.execute('UPDATE budget SET next_eligible=? WHERE id=1', (next_eligible,))
                                db.execute("UPDATE tickets SET status='running', acquired_at=? WHERE ticket=?", (now, ticket))
                                record.update(status='running', acquired_at=now,
                                              wait_seconds=time.monotonic() - started,
                                              next_eligible_at=next_eligible)
                                acquired = True
                    finally:
                        if not acquired:
                            fcntl.flock(global_fd, fcntl.LOCK_UN)
                if acquired:
                    break
                if time.monotonic() - started >= self.wait_timeout:
                    status = 'timed_out'
                    raise TimeoutError('Timed out waiting for shared native grading queue')
                time.sleep(min(.02, max(0, self.wait_timeout - (time.monotonic() - started))))
            lease = Lease(self, ticket, global_fd, record)
            try:
                yield lease
                status = 'completed'
            except BaseException as exc:
                status = 'interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed'
                raise
        finally:
            try:
                if registered:
                    finished = time.time()
                    with self._db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        if record['throttle_count']:
                            db.execute('UPDATE budget SET next_eligible=MAX(next_eligible, ?) WHERE id=1',
                                       (finished + self.cooldown_seconds,))
                        db.execute('UPDATE tickets SET status=?, finished_at=? WHERE ticket=?', (status, finished, ticket))
                        record.update(status=status, finished_at=finished,
                                      next_eligible_at=db.execute('SELECT next_eligible FROM budget WHERE id=1').fetchone()[0])
            finally:
                if lease:
                    lease._active = False
                # Close without LOCK_UN: an inherited native-child fd must retain the
                # shared open-file-description lock if this process exits first.
                if global_fd is not None:
                    os.close(global_fd)
                os.close(ticket_fd)
