"""Negative controls from the final review; all data and grades are local fixtures."""
import copy
import json
import sys

import pytest

from adk_eval_harness import runner
from adk_eval_harness.gate import digest, evaluate, json_equal, observation_checks, read, trace_checks
from adk_eval_harness.reporting import build_report
from tests.test_reporting import fixture, save
from tests.test_recovery_contract import setup_pack, SyntheticCommands, install
from scripts import package_reference


@pytest.mark.parametrize('filename', ['turn_0.json', 'state_0.json', 'runner_metadata.json'])
@pytest.mark.parametrize('damage', ['changed', 'missing'])
def test_gate_report_finish_and_cli_agree_on_damaged_telemetry(tmp_path, monkeypatch, filename, damage):
    run = fixture(tmp_path)
    folder = run / 'suite/sessions/case-1'
    for name in ['state_0.json', 'runner_metadata.json']:
        save(folder / name, {'state': {'approved': True}})
    manifest = read(run / 'manifest.json')
    manifest.update(telemetry_sha256={str(p.relative_to(run)): digest(p) for p in folder.glob('*.json')},
                    config_sha256='config', source_sha256={}, plan={'per_suite': {'suite': {}}})
    save(run / 'manifest.json', manifest)
    assert evaluate(run, run / 'policy.json')['passed']
    target = folder / filename
    if damage == 'missing':
        target.unlink()
    else:
        target.write_text(target.read_text() + '\n')
    gate, report = evaluate(run, run / 'policy.json'), build_report(run)
    assert gate['passed'] is report['passed'] is False
    assert gate['evaluation_complete'] is report['evaluation_complete'] is False
    assert any('Telemetry integrity failure' in e for e in gate['errors'])
    assert runner.finish(run, manifest) == 1
    config = {'_path': tmp_path / 'config.json', 'policy': run / 'policy.json', 'agent': manifest['agent']}
    monkeypatch.setattr(runner, 'load', lambda path: config)
    monkeypatch.setattr(runner, 'source_hashes', lambda config: {})
    real_digest = runner.digest
    monkeypatch.setattr(runner, 'digest', lambda path: 'config' if path == config['_path'] else real_digest(path))
    monkeypatch.setattr(sys, 'argv', ['harness', 'check', '--config', str(config['_path']), '--output', str(run)])
    assert runner.main() == 1


@pytest.mark.parametrize('damage', ['changed', 'missing', 'unbound_extra'])
def test_recovery_rejects_damaged_raw_evidence_before_any_grader(tmp_path, monkeypatch, damage):
    config = setup_pack(tmp_path)
    commands = SyntheticCommands()
    install(monkeypatch, commands)
    run = tmp_path / 'original'
    assert runner.run(config, run) == 1
    path = run / 'fixture/sessions/valid-zero/state_0.json'
    save(path, {'state': {'approved': True}})
    manifest = read(run / 'manifest.json')
    manifest['telemetry_sha256'] = {str(path.relative_to(run)): digest(path)}
    save(run / 'manifest.json', manifest)
    if damage == 'changed':
        path.write_text('{}')
    elif damage == 'missing':
        path.unlink()
    else:
        save(path.with_name('turn_1.json'), {'events': []})
    before = copy.deepcopy(commands.grade_requests)
    with pytest.raises(ValueError, match='Telemetry integrity failure'):
        runner.recover(config, run, tmp_path / 'recovered')
    assert commands.grade_requests == before and commands.generated == 1
    assert not (tmp_path / 'recovered').exists()
    assert not (run / 'recovery-link.json').exists()


@pytest.mark.parametrize('actual,expected', [(1, True), (0, False), (True, 1), (False, 0),
                                           ({'nested': [1]}, {'nested': [True]})])
def test_exact_json_checks_reject_boolean_number_substitution(actual, expected):
    reply = {'parts': [{'text': json.dumps({'approved': actual})}]}
    case = {'responses': [{'response': reply}], 'agent_data': {'turns': [{'turn_index': 0, 'events': [
        {'author': 'fixture', 'content': reply, 'state_delta': {'approved': actual}}]}]}}
    rules = {'json_response_expected': {'approved': expected}, 'turns': {'0': {
        'json_response_expected': {'approved': expected}, 'state_equals': {'approved': expected}}}}
    assert len(trace_checks(case, rules)) == 3
    assert len(observation_checks([{'name': 'authorize', 'args': {'approved': actual}}],
                [{'name': 'authorize', 'response': {'approved': actual}}],
                {'calls': [{'name': 'authorize', 'args': {'approved': expected}}],
                 'results': [{'name': 'authorize', 'values': {'approved': expected}}]})) == 2
    assert not json_equal(actual, expected)
    assert json_equal(expected, expected)


def test_json_numbers_preserve_numeric_equality():
    assert json_equal({'a': [1, 0.5, None, True]}, {'a': [1.0, 0.5, None, True]})
    assert not json_equal([True], [1.0])
    assert not json_equal({}, [])


@pytest.mark.parametrize('kind', ['external_file', 'internal_file', 'external_directory', 'dangling', 'listed_file', 'root_directory'])
def test_publication_rejects_symlinks_before_writing_zip(tmp_path, monkeypatch, kind):
    root = tmp_path / 'repo'; root.mkdir()
    folder = root / 'examples'; folder.mkdir()
    outside = tmp_path / 'external'; outside.mkdir()
    (outside / 'data.txt').write_text('SYNTHETIC external content')
    (folder / 'normal.txt').write_text('public')
    link = folder / 'link.txt'
    target = outside / 'data.txt'
    if kind == 'internal_file':
        target = folder / 'normal.txt'
    elif kind == 'external_directory':
        target = outside
    elif kind == 'dangling':
        target = outside / 'missing'
    elif kind == 'root_directory':
        link = root / 'linked_examples'; target = outside
    link.symlink_to(target)
    monkeypatch.setattr(package_reference, 'ROOT', root)
    monkeypatch.setattr(package_reference, 'FILES', [str(link.relative_to(root))] if kind == 'listed_file' else [])
    monkeypatch.setattr(package_reference, 'FOLDERS', ['linked_examples'] if kind == 'root_directory' else ['examples'])
    output = tmp_path / 'public.zip'
    monkeypatch.setattr(sys, 'argv', ['package', '--output', str(output)])
    with pytest.raises(ValueError, match='Symlink'):
        package_reference.main()
    assert not output.exists()


@pytest.mark.parametrize('stage', ['snapshot', 'create', 'metadata', 'cleanup', 'preflight'])
def test_session_lifecycle_deadlines_preserve_failure_evidence(tmp_path, stage):
    import asyncio
    import time
    from google.adk.agents import BaseAgent
    from adk_eval_harness.generation import generate
    from tests.test_telemetry import ScriptedRunner, event
    agent_runner = ScriptedRunner([event('complete')])
    async def stuck(*args, **kwargs):
        await asyncio.sleep(60)
    setattr(agent_runner, {'snapshot': 'get_session', 'create': 'create_session',
                          'metadata': 'validate_agent_metadata', 'cleanup': 'close',
                          'preflight': 'validate_agent_metadata'}[stage], stuck)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(generate([{'eval_case_id': 'one', 'messages': ['hello']}], tmp_path / 'traces.json',
                             BaseAgent(name='fixture'), runner_factory=lambda **kwargs: agent_runner,
                             turn_timeout=.03, session_timeout=.03, cleanup_timeout=.03,
                             metadata_only_agent=stage == 'preflight'))
    assert time.monotonic() - start < 2
    assert not (tmp_path / 'traces.json').exists()
    if stage == 'snapshot':
        turn = read(tmp_path / 'sessions/one/turn_0.json')
        assert turn['telemetry']['status'] == 'error' and turn['telemetry']['error_class'] == 'TimeoutError'
        assert turn['events']
    if stage == 'cleanup':
        assert read(tmp_path / 'sessions/one/cleanup_error.json')['error_class'] == 'TimeoutError'
    if stage != 'preflight':
        assert read(tmp_path / 'sessions/one/case_error.json')['error_class'] == 'TimeoutError'
    assert not read(tmp_path / 'progress.json')['complete']


def test_cleanup_timeout_retains_primary_failure_and_partial_turn(tmp_path):
    import asyncio
    from google.adk.agents import BaseAgent
    from adk_eval_harness.generation import generate
    from tests.test_telemetry import ScriptedRunner, event
    agent_runner = ScriptedRunner([event('partial', partial=True), RuntimeError('primary')])
    async def stuck():
        await asyncio.sleep(60)
    agent_runner.close = stuck
    with pytest.raises(RuntimeError, match='primary') as captured:
        asyncio.run(generate([{'eval_case_id': 'one', 'messages': ['hello']}], tmp_path / 'traces.json',
                             BaseAgent(name='fixture'), runner_factory=lambda **kwargs: agent_runner,
                             cleanup_timeout=.03))
    assert 'Could not close evaluation runner: TimeoutError' in captured.value.__notes__
    cleanup = read(tmp_path / 'sessions/one/cleanup_error.json')
    assert cleanup['primary_error_class'] == 'RuntimeError'
    assert cleanup['error_class'] == 'TimeoutError'
    assert read(tmp_path / 'sessions/one/turn_0.json')['events']


def test_source_package_excludes_local_run_evidence(tmp_path, monkeypatch):
    import zipfile
    root = tmp_path / 'repo'
    (root / 'examples').mkdir(parents=True)
    (root / 'examples/fixture.py').write_text('# included source\n')
    evidence = root / 'artifacts/evidence'
    evidence.mkdir(parents=True)
    (evidence / 'report.json').write_text('{"local_only": true}')
    monkeypatch.setattr(package_reference, 'ROOT', root)
    monkeypatch.setattr(package_reference, 'FILES', [])
    output = tmp_path / 'source.zip'
    monkeypatch.setattr(sys, 'argv', ['package', '--output', str(output)])
    package_reference.main()
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ['google-agent-evals-demo/examples/fixture.py']
    assert (evidence / 'report.json').exists()
