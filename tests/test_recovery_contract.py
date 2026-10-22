"""Runner recovery integration; every native-shaped score here is SYNTHETIC.

These fixtures verify orchestration and evidence preservation, not hosted quality.
"""
from pathlib import Path
import os
import sys

import pytest

import yaml

from adk_eval_harness import runner
from adk_eval_harness.config import load
from adk_eval_harness.gate import read

METRIC = 'final_response_match_v2'


def setup_pack(tmp_path, *, max_attempts=3, metrics=None, batch_size=100, grading_timeout=1800):
    metrics = metrics or [METRIC]
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'fixture_agent.py').write_text('# Synthetic orchestration fixture; never imported.\n')
    cases = [{'eval_case_id': cid, 'messages': ['Say hello'],
              'reference': {'response': {'role': 'model', 'parts': [{'text': 'hello'}]}}}
             for cid in ('valid-zero', 'unavailable')]
    runner.save(project / 'dataset.json', cases)
    (project / 'metrics.yaml').write_text(yaml.safe_dump({'metrics_to_run': metrics}))
    runner.save(project / 'policy.json', {'schema_version': 2, 'thresholds': {metric: 1 for metric in metrics},
                'suites': {'fixture': {'metrics': metrics, 'cases': {c['eval_case_id']: {} for c in cases}}}})
    runner.save(project / 'config.json', {
        'schema_version': 1, 'framework': 'adk', 'project_root': '.',
        'agent': 'fixture_agent:root_agent', 'app_name': 'fixture', 'user_id': 'fixture',
        'source_roots': ['fixture_agent.py'], 'policy': 'policy.json',
        'suites': {'fixture': {'dataset': 'dataset.json', 'metrics': 'metrics.yaml'}},
        'grading_queue': str(tmp_path / 'queue'), 'queue_budget_qps': 10000,
        'queue_cooldown_seconds': 0, 'qps': 10000, 'grading_batch_size': batch_size, 'grading_timeout': grading_timeout,
        'max_grading_attempts': max_attempts})
    return load(project / 'config.json')


def synthetic_trace(case):
    user = {'role': 'user', 'parts': [{'text': case['messages'][0]}]}
    reply = {'role': 'model', 'parts': [{'text': 'hello'}]}
    return {'eval_case_id': case['eval_case_id'], 'reference': case['reference'],
            'agent_data': {'agents': {}, 'turns': [{'turn_index': 0, 'turn_id': 'turn_0',
                'events': [{'author': 'user', 'content': user}, {'author': 'fixture', 'content': reply}]}]},
            'responses': [{'response': reply}]}


class SyntheticCommands:
    """Simulates only CLI process artifacts and records; never invokes a native judge."""
    def __init__(self, mode='partial'):
        self.mode = mode
        self.generated = 0
        self.grade_requests = []
        self.grade_jobs = []
        self.pair_counts = {}
        self.first_results = None

    def __call__(self, argv, log, config, records, timeout=1800, *, pass_fds=(), on_update=None):
        argv = list(map(str, argv))
        log = Path(log)
        log.write_text('SYNTHETIC RUNNER CONTRACT FIXTURE. No cloud call.\n')
        record = {'argv': argv, 'exit_code': 0, 'log': log.name,
                  'fixture_provenance': 'synthetic_runner_contract'}
        records.append(record)
        if 'generate' in argv:
            self.generated += 1
            source = read(config['suites']['fixture']['dataset'])
            runner.save(Path(argv[argv.index('--output') + 1]),
                        {'eval_cases': [synthetic_trace(c) for c in source]})
        elif argv[1:3] == ['eval', 'grade']:
            assert pass_fds, 'Native grader must inherit its shared queue lease'
            trace = read(Path(argv[argv.index('--traces') + 1]))
            metric = yaml.safe_load(Path(argv[argv.index('--config') + 1]).read_text())['metrics_to_run']
            assert len(metric) == 1, 'Every native job selects one metric'
            metric = metric[0]
            ids = [case['eval_case_id'] for case in trace['eval_cases']]
            self.grade_requests.append(ids)
            self.grade_jobs.append((metric, ids))
            if self.mode == 'interrupted' and len(self.grade_requests) == 1:
                record['exit_code'] = 130
                if on_update:
                    on_update()
                raise KeyboardInterrupt('Synthetic grading interruption')
            rows, valid = [], 0
            for index, cid in enumerate(ids):
                pair = (cid, metric)
                self.pair_counts[pair] = self.pair_counts.get(pair, 0) + 1
                unavailable = cid == 'unavailable' and metric == METRIC and (self.pair_counts[pair] == 1 or self.mode == 'always_unavailable')
                if self.mode == 'interrupted':
                    unavailable = False
                metric_value = {'metric_name': metric, 'score': None if unavailable else (0.0 if cid == 'valid-zero' and metric == METRIC else 1.0)}
                if unavailable:
                    metric_value['error_message'] = 'SYNTHETIC 429 judge unavailable'
                else:
                    valid += 1
                rows.append({'eval_case_index': index, 'response_candidate_results': [
                    {'response_index': 0, 'metric_results': {metric: metric_value}}]})
            result = {'evaluation_dataset': [trace], 'eval_case_results': rows,
                      'summary_metrics': [{'metric_name': metric, 'num_cases_total': len(ids),
                          'num_cases_valid': valid, 'num_cases_error': len(ids) - valid}]}
            path = Path(argv[argv.index('--output') + 1]) / 'results_synthetic_fixture.json'
            runner.save(path, result)
            if self.first_results is None:
                self.first_results = (path, path.read_bytes())
        if on_update:
            on_update()
        return record


def install(monkeypatch, commands):
    monkeypatch.setattr(runner, 'command', commands)
    original_version = runner.importlib.metadata.version
    monkeypatch.setattr(runner.importlib.metadata, 'version', lambda name: runner.VERSIONS[name] if name in runner.VERSIONS else original_version(name))


def test_recovery_only_regrades_unavailable_pairs_and_keeps_zero(monkeypatch, tmp_path):
    config = setup_pack(tmp_path)
    commands = SyntheticCommands()
    install(monkeypatch, commands)
    original, recovered = tmp_path / 'original', tmp_path / 'recovered'
    assert runner.run(config, original) == 1
    first_gate = read(original / 'gate.json')
    assert not first_gate['evaluation_complete']
    original_manifest = (original / 'manifest.json').read_bytes()
    original_traces = (original / 'fixture/traces.json').read_bytes()
    assert runner.recover(config, original, recovered) == 1  # Legitimate zero remains blocking.
    report = read(recovered / 'gate.json')
    assert report['evaluation_complete'] and not report['passed']
    scores = {row['case']: row['score'] for row in report['scores']}
    assert scores == {'valid-zero': 0.0, 'unavailable': 1.0}
    assert commands.generated == 1
    assert commands.grade_requests == [['valid-zero', 'unavailable'], ['unavailable']]
    assert (original / 'manifest.json').read_bytes() == original_manifest
    assert (original / 'fixture/traces.json').read_bytes() == original_traces
    path, contents = commands.first_results
    assert path.read_bytes() == contents
    # Recovery must retain the initial error evidence as well as the successful attempt.
    retained = [p for p in recovered.rglob('results_synthetic_fixture.json') if p.read_bytes() == contents]
    assert retained


def test_interrupted_grading_is_recoverable_without_agent_reexecution(monkeypatch, tmp_path):
    config = setup_pack(tmp_path)
    commands = SyntheticCommands('interrupted')
    install(monkeypatch, commands)
    original, recovered = tmp_path / 'interrupted', tmp_path / 'recovered'
    assert runner.run(config, original) == 1
    assert read(original / 'manifest.json')['generation_complete']
    assert runner.recover(config, original, recovered) == 1
    assert commands.generated == 1
    assert commands.grade_requests == [['valid-zero', 'unavailable'], ['valid-zero', 'unavailable']]
    report = read(recovered / 'gate.json')
    assert report['evaluation_complete'] and not report['passed']


def test_recovery_attempt_limit_prevents_unbounded_grader_retries(monkeypatch, tmp_path):
    config = setup_pack(tmp_path, max_attempts=2)
    commands = SyntheticCommands('always_unavailable')
    install(monkeypatch, commands)
    initial, retry, exhausted = (tmp_path / name for name in ('initial', 'retry', 'exhausted'))
    assert runner.run(config, initial) == 1
    assert runner.recover(config, initial, retry) == 1
    try:
        outcome = runner.recover(config, retry, exhausted)
        assert outcome == 1
    except ValueError:
        pass
    assert commands.generated == 1
    assert commands.grade_requests == [['valid-zero', 'unavailable'], ['unavailable']]


def test_tampered_source_trace_never_reaches_grader(monkeypatch, tmp_path):
    config = setup_pack(tmp_path)
    commands = SyntheticCommands()
    install(monkeypatch, commands)
    initial = tmp_path / 'initial'
    assert runner.run(config, initial) == 1
    trace = initial / 'fixture/traces.json'
    trace.write_text(trace.read_text() + '\n')
    before = len(commands.grade_requests)
    try:
        outcome = runner.recover(config, initial, tmp_path / 'tampered')
        assert outcome == 1
    except ValueError:
        pass
    assert len(commands.grade_requests) == before
    assert commands.generated == 1


def test_multimetric_batches_retry_only_failed_metric_pair(monkeypatch, tmp_path):
    config = setup_pack(tmp_path, metrics=[METRIC, 'safety_v1'], batch_size=1)
    commands = SyntheticCommands()
    install(monkeypatch, commands)
    original, recovered = tmp_path / 'original', tmp_path / 'recovered'
    assert runner.run(config, original) == 1
    assert commands.grade_jobs == [(METRIC, ['valid-zero']), (METRIC, ['unavailable']),
                                   ('safety_v1', ['valid-zero']), ('safety_v1', ['unavailable'])]
    assert runner.recover(config, original, recovered) == 1
    assert commands.grade_jobs[4:] == [(METRIC, ['unavailable'])]
    assert commands.generated == 1
    report = read(recovered / 'gate.json')
    assert report['evaluation_complete'] and len(report['scores']) == 4
    assert next(s['score'] for s in report['scores'] if s['case'] == 'valid-zero' and s['metric'] == METRIC) == 0


def test_real_timed_out_grading_process_releases_locks_and_recovers(monkeypatch, tmp_path):
    config = setup_pack(tmp_path, grading_timeout=.3)
    marker = tmp_path / 'fake-grader.pid'
    executable = tmp_path / 'synthetic-native-cli'
    executable.write_text(f'#!{sys.executable}\nimport os, time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n')
    executable.chmod(0o700)
    real_command = runner.command
    commands = SyntheticCommands('interrupted')
    install(monkeypatch, commands)
    timed_out = False
    def dispatch(argv, log, config, records, timeout=1800, **kwargs):
        nonlocal timed_out
        argv = list(map(str, argv))
        if argv[1:3] == ['eval', 'grade'] and not timed_out:
            timed_out = True
            commands.grade_requests.append(['valid-zero', 'unavailable'])
            # Actual subprocess timeout and inherited locks; the executable is a labeled local fixture.
            return real_command([str(executable), *argv[1:]], log, config, records, timeout, **kwargs)
        return commands(argv, log, config, records, timeout, **kwargs)
    monkeypatch.setattr(runner, 'command', dispatch)
    original, recovered = tmp_path / 'timed-out', tmp_path / 'recovered'
    assert runner.run(config, original) == 1
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    manifest = read(original / 'manifest.json')
    assert manifest['suites']['fixture']['attempts'][0]['command']['exit_code'] == 124
    assert runner.recover(config, original, recovered) == 1
    assert commands.generated == 1
    assert commands.grade_requests == [['valid-zero', 'unavailable'], ['valid-zero', 'unavailable']]
    assert read(recovered / 'gate.json')['evaluation_complete']


def test_user_cancellation_stops_new_batches_and_remains_recoverable(monkeypatch, tmp_path):
    config = setup_pack(tmp_path, batch_size=1)
    commands = SyntheticCommands('interrupted')
    install(monkeypatch, commands)
    original, recovered = tmp_path / 'cancelled', tmp_path / 'recovered'
    assert runner.run(config, original) == 1
    # The user's interrupt must not schedule the second case after cancelling the first.
    assert commands.grade_requests == [['valid-zero']]
    manifest = read(original / 'manifest.json')
    assert manifest['generation_complete']
    assert not manifest['errors']
    assert runner.recover(config, original, recovered) == 1
    assert commands.grade_requests == [['valid-zero'], ['valid-zero'], ['unavailable']]
    assert commands.generated == 1
    assert read(recovered / 'gate.json')['evaluation_complete']
