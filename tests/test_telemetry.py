"""Client timing instrumentation; scripted events avoid hosted latency/token claims."""
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.genai import types

from adk_eval_harness import generation


def event(text, *, thought=False, role='model', partial=False, usage=None):
    return Event(author='fixture', partial=partial, timestamp=10000000000,
                 content=types.Content(role=role, parts=[types.Part(text=text, thought=thought)]),
                 usage_metadata=usage)


class ScriptedRunner:
    def __init__(self, script, *, close_error=False, metadata_error=False):
        self.script = script
        self.closed = False
        self.close_error = close_error
        self.metadata_error = metadata_error
        self.session_service = self
        self.order = []
        self.transport_stats = {'requests':0}

    async def validate_agent_metadata(self, metadata):
        self.order.append('metadata')
        if self.metadata_error:
            raise ValueError('remote metadata differs')
        assert 'fixture' in metadata
        self.observed_metadata = {'schema_version':1, 'source_sha256':{'fixture.py':'frozen'},
                                  'deployment_revision':'test-revision'}

    async def create_session(self, **kwargs):
        self.order.append('create_session')
        self.session = SimpleNamespace(id='test-session', state=kwargs['state'])
        return self.session

    async def get_session(self, **kwargs):
        self.order.append('get_session')
        return self.session

    async def run_async(self, **kwargs):
        self.order.append('run')
        self.transport_stats['requests'] += 1
        for item in self.script:
            await asyncio.sleep(0.002)
            if isinstance(item, BaseException):
                raise item
            if item == 'timeout':
                await asyncio.sleep(60)
            else:
                yield item

    async def close(self):
        self.closed = True
        if self.close_error:
            raise OSError('private cleanup details')


def run(tmp_path, runner, *, timeout=1, messages=None):
    return asyncio.run(generation.generate(
        [{'eval_case_id':'one', 'messages':messages or ['hello']}], tmp_path/'traces.json',
        BaseAgent(name='fixture'), turn_timeout=timeout, runner_factory=lambda **kwargs:runner))


def recorded(tmp_path, index=0):
    return json.loads((tmp_path/f'sessions/one/turn_{index}.json').read_text())


def test_client_timing_excludes_thought_and_tool_text_and_preserves_usage(tmp_path):
    usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=3, candidates_token_count=5,
                                                       total_token_count=8)
    runner = ScriptedRunner([event('private thought', thought=True), event('tool payload', role='user'),
                             event('partial visible answer', partial=True), event('final answer', usage=usage)])
    run(tmp_path, runner)
    data = recorded(tmp_path)
    telemetry = data['telemetry']
    assert telemetry['status'] == 'success' and telemetry['error_class'] is None
    assert telemetry['event_count'] == 4
    assert 0 <= telemetry['time_to_first_event_seconds'] < telemetry['time_to_first_text_seconds'] < telemetry['duration_seconds']
    assert datetime.fromisoformat(telemetry['started_at']).utcoffset() == timezone.utc.utcoffset(None)
    assert telemetry['clock'] == 'client_monotonic'
    assert telemetry['scope'] == 'turn_execution_and_state_snapshot'
    assert data['events'][-1]['usage_metadata'] == usage.model_dump(mode='json', exclude_none=True)
    assert 'tokens' not in telemetry  # No inferred or judge-token accounting.
    assert data['transport_stats_after_turn'] == {'requests':1}
    assert runner.closed


def test_partial_failure_records_error_class_without_exception_text(tmp_path):
    runner = ScriptedRunner([event('partial', partial=True), RuntimeError('SECRET project/customer payload')])
    with pytest.raises(RuntimeError, match='SECRET'):
        run(tmp_path, runner)
    telemetry = recorded(tmp_path)['telemetry']
    assert telemetry['status'] == 'error' and telemetry['error_class'] == 'RuntimeError'
    assert telemetry['event_count'] == 1 and telemetry['time_to_first_text_seconds'] is not None
    assert 'SECRET' not in json.dumps(telemetry)
    assert not (tmp_path/'traces.json').exists()


def test_timeout_before_first_event_records_unknown_first_times(tmp_path):
    with pytest.raises(TimeoutError):
        run(tmp_path, ScriptedRunner(['timeout']), timeout=0.01)
    telemetry = recorded(tmp_path)['telemetry']
    assert telemetry['status'] == 'error' and telemetry['error_class'] == 'TimeoutError'
    assert telemetry['event_count'] == 0
    assert telemetry['time_to_first_event_seconds'] is None
    assert telemetry['time_to_first_text_seconds'] is None


def test_missing_final_response_is_recorded_as_error(tmp_path):
    with pytest.raises(ValueError, match='no final response'):
        run(tmp_path, ScriptedRunner([event('thought only', thought=True)]))
    telemetry = recorded(tmp_path)['telemetry']
    assert telemetry['status'] == 'error' and telemetry['error_class'] == 'ValueError'
    assert telemetry['time_to_first_event_seconds'] is not None
    assert telemetry['time_to_first_text_seconds'] is None


def test_adk_error_event_is_retained_and_turn_fails(tmp_path):
    error = Event(author='fixture', error_code='ERR', error_message='native diagnostic')
    with pytest.raises(RuntimeError, match='error event'):
        run(tmp_path, ScriptedRunner([error]))
    data = recorded(tmp_path)
    assert data['telemetry']['error_class'] == 'RuntimeError'
    assert data['events'][0]['error_message'] == 'native diagnostic'


def test_storage_and_cleanup_errors_do_not_mask_original_failure(tmp_path, monkeypatch):
    real_save = generation.save
    def broken_save(path, value):
        if path.name == 'turn_0.json':
            raise OSError('private storage details')
        return real_save(path, value)
    monkeypatch.setattr(generation, 'save', broken_save)
    with pytest.raises(RuntimeError, match='original failure') as captured:
        run(tmp_path, ScriptedRunner([RuntimeError('original failure')], close_error=True))
    assert captured.value.__notes__ == ['Could not persist turn evidence: OSError',
                                        'Could not close evaluation runner: OSError']


def test_telemetry_write_failure_on_success_is_not_silently_ignored(tmp_path, monkeypatch):
    real_save = generation.save
    def broken_save(path, value):
        if path.name == 'turn_0.json':
            raise OSError('cannot save evidence')
        return real_save(path, value)
    monkeypatch.setattr(generation, 'save', broken_save)
    with pytest.raises(OSError, match='cannot save evidence'):
        run(tmp_path, ScriptedRunner([event('complete')]))
    assert not (tmp_path/'traces.json').exists()


def test_metadata_is_validated_before_remote_session_and_bound_to_evidence(tmp_path):
    runner = ScriptedRunner([event('complete')])
    run(tmp_path, runner)
    assert runner.order[:3] == ['metadata', 'create_session', 'run']
    saved = json.loads((tmp_path/'sessions/one/runner_metadata.json').read_text())
    assert saved['metadata'] == runner.observed_metadata


def test_bad_remote_metadata_prevents_execution_and_closes_runner(tmp_path):
    runner = ScriptedRunner([event('must not execute')], metadata_error=True)
    with pytest.raises(ValueError, match='remote metadata differs'):
        run(tmp_path, runner)
    assert runner.order == ['metadata']
    assert runner.closed
    assert not list((tmp_path/'sessions').glob('*/turn_*.json'))
    assert json.loads((tmp_path/'sessions/one/case_error.json').read_text())['error_class'] == 'ValueError'


def test_multiturn_records_separate_timings_and_transport_snapshots(tmp_path):
    runner = ScriptedRunner([event('complete')])
    run(tmp_path, runner, messages=['hello','again'])
    first, second = recorded(tmp_path, 0), recorded(tmp_path, 1)
    assert first['session_id'] == second['session_id']
    assert first['telemetry']['event_count'] == second['telemetry']['event_count'] == 1
    assert first['transport_stats_after_turn'] == {'requests':1}
    assert second['transport_stats_after_turn'] == {'requests':2}
    assert first['telemetry']['started_at'] < second['telemetry']['started_at']


def test_cancellation_preserves_partial_telemetry(tmp_path):
    runner = ScriptedRunner([event('partial', partial=True), asyncio.CancelledError('private cancellation context')])
    with pytest.raises(asyncio.CancelledError):
        run(tmp_path, runner)
    telemetry = recorded(tmp_path)['telemetry']
    assert telemetry['status'] == 'error' and telemetry['error_class'] == 'CancelledError'
    assert telemetry['event_count'] == 1
    assert 'private cancellation context' not in json.dumps(telemetry)
    assert runner.closed


def test_state_snapshot_failure_is_in_turn_telemetry(tmp_path):
    runner = ScriptedRunner([event('complete')])
    async def unavailable(**kwargs):
        raise ConnectionError('private endpoint detail')
    runner.get_session = unavailable
    with pytest.raises(ConnectionError):
        run(tmp_path, runner)
    telemetry = recorded(tmp_path)['telemetry']
    assert telemetry['status'] == 'error' and telemetry['error_class'] == 'ConnectionError'
    assert telemetry['event_count'] == 1
