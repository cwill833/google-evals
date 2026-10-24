"""Independent lineage and writer-lifecycle review; no hosted model calls."""
import copy
import subprocess
import sys

import pytest

from adk_eval_harness import runner
from adk_eval_harness.gate import digest, read
from adk_eval_harness.lineage import verify_lineage
from test_recovery_contract import SyntheticCommands, install, setup_pack


def chain(root):
    parent = {'schema_version':3, 'generation_complete':True, 'errors':[],
              'config_sha256':'configuration', 'policy_sha256':'policy', 'source_sha256':{'source':'hash'},
              'agent':'fixture:agent', 'versions':runner.VERSIONS, 'commands':[{'exit_code':0}],
              'plan':{'case_count':1}, 'suites':{'suite':{
                  'trace':'suite/traces.json', 'trace_sha256':'original-trace',
                  'attempts':[{'metric':'final_response_match', 'command':{'exit_code':0},
                               'results':'suite/attempt/results.json', 'results_sha256':'original-result'}]}}}
    runner.save(root/'history/parent.json', parent)
    child = copy.deepcopy(parent)
    child['parent'] = {'manifest':'history/parent.json', 'manifest_sha256':digest(root/'history/parent.json')}
    return parent, child


@pytest.mark.parametrize('change', [
    lambda c:c['suites']['suite']['attempts'].clear(),
    lambda c:c['suites']['suite']['attempts'][0].update(results_sha256='replacement'),
    lambda c:c['suites']['suite']['attempts'][0]['command'].update(exit_code=1),
    lambda c:c['suites']['suite'].update(trace_sha256='replacement')])
def test_child_cannot_drop_or_rewrite_original_attempts(tmp_path, change):
    _, child = chain(tmp_path)
    change(child)
    with pytest.raises(ValueError, match='changed|dropped'):
        verify_lineage(tmp_path, child)


def test_parent_manifest_hash_detects_ancestor_changes(tmp_path):
    parent, child = chain(tmp_path)
    parent['suites']['suite']['attempts'].clear()
    runner.save(tmp_path/'history/parent.json', parent)
    with pytest.raises(ValueError, match='hash mismatch'):
        verify_lineage(tmp_path, child)


def test_parent_link_cannot_escape_run(tmp_path):
    _, child = chain(tmp_path)
    child['parent']['manifest'] = '../other/manifest.json'
    with pytest.raises(ValueError, match='path escapes run'):
        verify_lineage(tmp_path, child)


def test_ancestor_execution_failure_cannot_be_erased(tmp_path):
    parent, child = chain(tmp_path)
    parent['errors'] = ['Source changed during evaluation']
    runner.save(tmp_path/'history/parent.json', parent)
    child['parent']['manifest_sha256'] = digest(tmp_path/'history/parent.json')
    with pytest.raises(ValueError, match='error|failure|corrupt'):
        verify_lineage(tmp_path, child)


def test_active_writer_prevents_recovery_before_any_work(tmp_path):
    source = tmp_path/'source'
    source.mkdir()
    with runner.writer_lock(source):
        with pytest.raises(ValueError, match='still active'):
            runner.recover({'_root':tmp_path}, source, tmp_path/'successor')
    assert not (tmp_path/'successor').exists()
    assert not (source/'recovery-link.json').exists()


def test_native_child_keeps_writer_lock_after_parent_releases_it(tmp_path):
    child = None
    try:
        with runner.writer_lock(tmp_path) as fd:
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], pass_fds=(fd,))
        assert child.poll() is None
        with pytest.raises(ValueError, match='still active'):
            with runner.writer_lock(tmp_path):
                pass
    finally:
        if child is not None:
            child.terminate()
            child.wait(timeout=5)
    with runner.writer_lock(tmp_path):
        pass


def test_original_run_cannot_spawn_multiple_recovery_successors(monkeypatch, tmp_path):
    config = setup_pack(tmp_path)
    commands = SyntheticCommands('always_unavailable')
    install(monkeypatch, commands)
    original, first = tmp_path/'original', tmp_path/'first'
    assert runner.run(config, original) == 1
    assert runner.recover(config, original, first) == 1
    previous_calls = len(commands.grade_requests)
    with pytest.raises(ValueError, match='already has a recovery successor'):
        runner.recover(config, original, tmp_path/'fork')
    assert len(commands.grade_requests) == previous_calls
    assert commands.generated == 1


def test_interrupted_attempt_with_unrecorded_results_never_reaches_grader(monkeypatch, tmp_path):
    config = setup_pack(tmp_path)
    commands = SyntheticCommands('interrupted')
    install(monkeypatch, commands)
    original = tmp_path/'original'
    assert runner.run(config, original) == 1
    manifest = read(original/'manifest.json')
    attempt = manifest['suites']['fixture']['attempts'][0]
    attempt['command'].update(exit_code=None, status='running')
    runner.save(original/'manifest.json', manifest)
    runner.save(original/attempt['grades_dir']/'results_unrecorded.json', {'ambiguous':'evidence'})
    previous_calls = len(commands.grade_requests)
    with pytest.raises(ValueError, match='undeclared or ambiguous'):
        runner.recover(config, original, tmp_path/'recovered')
    assert len(commands.grade_requests) == previous_calls
    assert commands.generated == 1


def test_cancellation_does_not_submit_later_batches(monkeypatch, tmp_path):
    config = setup_pack(tmp_path)
    config['grading_batch_size'] = 1
    commands = SyntheticCommands('interrupted')
    install(monkeypatch, commands)
    assert runner.run(config, tmp_path/'original') == 1
    assert commands.grade_requests == [['valid-zero']]


def test_failed_successor_copy_does_not_consume_source_recovery_claim(monkeypatch, tmp_path):
    config = setup_pack(tmp_path)
    commands = SyntheticCommands()
    install(monkeypatch, commands)
    original = tmp_path/'original'
    assert runner.run(config, original) == 1
    original_copytree = runner.shutil.copytree
    def fail_copy(*args, **kwargs):
        raise OSError('Synthetic disk-full copy failure')
    monkeypatch.setattr(runner.shutil, 'copytree', fail_copy)
    with pytest.raises(OSError, match='disk-full'):
        runner.recover(config, original, tmp_path/'failed-copy')
    assert not (original/'recovery-link.json').exists()
    monkeypatch.setattr(runner.shutil, 'copytree', original_copytree)
    assert runner.recover(config, original, tmp_path/'good-copy') == 1
    assert commands.generated == 1


@pytest.mark.parametrize('changed', ['versions', 'config_sha256', 'policy_sha256', 'source_sha256', 'agent', 'commands', 'plan'])
def test_recovery_cannot_change_provenance_fields(tmp_path, changed):
    _, child = chain(tmp_path)
    child[changed] = 'forged'
    with pytest.raises(ValueError, match='changed original'):
        verify_lineage(tmp_path, child)
