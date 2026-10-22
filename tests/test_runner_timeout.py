"""A timed-out native command must not leave a running descendant behind."""
import sys
import time
import pytest
from scripts import run_evals


@pytest.mark.parametrize('implementation', ['legacy', 'reusable'])
def test_timeout_stops_descendant(tmp_path, monkeypatch, implementation):
    heartbeat = tmp_path / 'heartbeat'
    child_code = (
        'import time; from pathlib import Path; '
        f'p=Path({str(heartbeat)!r}); '\
        '\nwhile True:\n p.write_text(str(time.time_ns())); time.sleep(0.02)'
    )
    stub = tmp_path / 'cli'
    stub.write_text(f'#!{sys.executable}\nimport subprocess,time\n'
                    f'subprocess.Popen([{sys.executable!r}, "-c", {child_code!r}])\n'
                    'time.sleep(60)\n')
    stub.chmod(0o755)
    monkeypatch.setattr(run_evals, 'CLI', stub)
    records = []
    with pytest.raises(RuntimeError, match='124'):
        if implementation == 'legacy':
            run_evals.command([], tmp_path / 'log', records, timeout=2.0)
        else:
            from adk_eval_harness.runner import command
            command([stub], tmp_path / 'log', {'_root':tmp_path}, records, timeout=2.0)
    assert records[0]['exit_code'] == 124
    assert heartbeat.exists()
    last = heartbeat.read_text()
    time.sleep(0.1)
    assert heartbeat.read_text() == last


def test_uncooperative_command_is_killed_after_bounded_grace(tmp_path):
    from adk_eval_harness.runner import command
    records = []
    start = time.monotonic()
    with pytest.raises(RuntimeError, match='124'):
        command([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(60)'],
                tmp_path / 'log', {'_root': tmp_path, 'shutdown_grace': .1}, records, timeout=.3)
    assert time.monotonic() - start < 3
    assert records[0]['shutdown']['forced_group_kill'] is True
    assert records[0]['exit_code'] == 124


def test_real_run_cli_interrupt_flushes_worker_evidence_and_cleanup(tmp_path):
    import json
    import os
    from pathlib import Path
    import signal
    import subprocess
    from scripts.load_test import prepare
    from adk_eval_harness.gate import read
    from adk_eval_harness.runner import save
    root = Path(__file__).resolve().parents[1]
    pack = tmp_path / 'pack'
    prepare(pack, 4, case_concurrency=2, delay=60)
    ready, closed = tmp_path / 'ready', tmp_path / 'closed'
    (pack / 'fixture.py').write_text(
        'from pathlib import Path\nfrom google.adk.runners import InMemoryRunner\n'
        'from examples.load.agent import create_agent\n'
        'class Runner(InMemoryRunner):\n'
        ' async def run_async(self, **kwargs):\n'
        f'  Path({str(ready)!r}).write_text("running")\n'
        '  async for event in super().run_async(**kwargs):\n   yield event\n'
        ' async def close(self):\n'
        '  await super().close()\n'
        f'  Path({str(closed)!r}).write_text("closed")\n')
    config = read(pack / 'config.json')
    config.update(project_root='.', agent='fixture:create_agent', runner_factory='fixture:Runner',
                  source_roots=['fixture.py'], shutdown_grace=3, cleanup_timeout=1, turn_timeout=90)
    save(pack / 'config.json', config)
    run = tmp_path / 'run'
    with (tmp_path / 'parent.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-m', 'adk_eval_harness', 'run', '--config',
                       str(pack / 'config.json'), '--output', str(run)], cwd=root,
                       env={**os.environ, 'PYTHONPATH': str(root)}, stdout=log, stderr=log,
                       start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            assert ready.exists(), (tmp_path / 'parent.log').read_text()
            os.kill(process.pid, signal.SIGINT)
            assert process.wait(timeout=10) != 0
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    manifest = read(run / 'manifest.json')
    assert not manifest['generation_complete'] and not manifest['suites']
    assert manifest['commands'][-1]['exit_code'] == 130
    assert manifest['commands'][-1]['shutdown']['reason'] == 'interrupt'
    assert closed.exists()
    assert not (run / 'load/traces.json').exists()
    progress = read(run / 'load/progress.json')
    assert list(progress['case_statuses'].values()).count('cancelled') == 2
    assert list(progress['case_statuses'].values()).count('not_started') == 2
    for turn in (run / 'load/sessions').glob('*/turn_0.json'):
        assert read(turn)['telemetry']['error_class'] == 'CancelledError'
    assert not read(run / 'gate.json')['passed']
    assert not read(run / 'report.json')['evaluation_complete']
