"""Real ADK scheduling, session isolation, failure and cancellation contracts."""
import asyncio
import json

import pytest
from google.adk.runners import InMemoryRunner
from google.adk.sessions import InMemorySessionService

from adk_eval_harness.generation import generate
from examples.load.agent import create_agent


def cases(count=9, turns=3):
    return [{'eval_case_id':f'case_{i}', 'messages':[json.dumps({
        'case_id':f'case_{i}', 'payload':'test', 'delay':.03 if i == 0 else .005})] * turns}
        for i in range(count)]


def read(path):
    return json.loads(path.read_text())


def test_pool_bounds_isolation_order_and_completion_journal(tmp_path):
    shared = InMemorySessionService()
    users, closed = [], []
    class Runner(InMemoryRunner):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.session_service = shared
        async def run_async(self, **kwargs):
            users.append(kwargs['user_id'])
            async for event in super().run_async(**kwargs):
                yield event
        async def close(self):
            closed.append(self)
            await super().close()
    output = tmp_path/'traces.json'
    asyncio.run(generate(cases(), output, None, agent_factory=create_agent,
                         runner_factory=Runner, case_concurrency=3))
    progress = read(tmp_path/'progress.json')
    assert progress['peak_active_cases'] == 3
    assert progress['complete'] and progress['completed_cases'] == 9
    assert len(closed) == len(set(users)) == 9
    trace = read(output)['eval_cases']
    assert [c['eval_case_id'] for c in trace] == [f'case_{i}' for i in range(9)]
    journal = [json.loads(line) for line in (tmp_path/'completed_cases.jsonl').read_text().splitlines()]
    assert journal[0]['eval_case_id'] != 'case_0'  # real overlap, not just configured capacity
    assert sorted(journal,key=lambda c:c['eval_case_id']) == sorted(trace,key=lambda c:c['eval_case_id'])
    session_ids = set()
    for i in range(9):
        snapshots = [read(tmp_path/f'sessions/case_{i}/state_{t}.json') for t in range(3)]
        assert [s['state']['user:count'] for s in snapshots] == [1,2,3]
        assert len({s['session_id'] for s in snapshots}) == 1
        session_ids.add(snapshots[0]['session_id'])
    assert len(session_ids) == 9


def test_failure_stops_dequeue_drains_inflight_and_never_retries(tmp_path):
    workload = cases()
    workload[0]['messages'] = [json.dumps({'case_id':'case_0','payload':'x','fail':True,'delay':.001})]
    with pytest.raises(RuntimeError, match='Injected load-test'):
        asyncio.run(generate(workload,tmp_path/'traces.json',None,
                    agent_factory=create_agent,case_concurrency=3))
    progress = read(tmp_path/'progress.json')
    assert not progress['complete'] and progress['failed_cases'] == 1
    assert progress['completed_cases'] == 2
    assert list(progress['case_statuses'].values()).count('not_started') == 6
    assert not (tmp_path/'traces.json').exists()
    assert read(tmp_path/'sessions/case_0/turn_0.json')['telemetry']['status'] == 'error'
    assert read(tmp_path/'sessions/case_0/case_error.json')['agent_retry_attempted'] is False
    assert len(list((tmp_path/'sessions').glob('*/turn_*.json'))) == 7
    assert len((tmp_path/'completed_cases.jsonl').read_text().splitlines()) == 2


def test_cancellation_closes_workers_and_keeps_failed_turns(tmp_path):
    async def exercise():
        task=asyncio.create_task(generate(cases(),tmp_path/'traces.json',None,
                                         agent_factory=create_agent,case_concurrency=3))
        await asyncio.sleep(.002)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(exercise())
    assert not (tmp_path/'traces.json').exists()
    progress = read(tmp_path/'progress.json')
    assert progress['failed_cases'] == 3
    assert list(progress['case_statuses'].values()).count('cancelled') == 3
    assert all(read(p)['telemetry']['error_class']=='CancelledError'
               for p in (tmp_path/'sessions').glob('*/turn_0.json'))


def test_singleton_factory_is_rejected_before_second_agent_run(tmp_path):
    singleton=create_agent()
    with pytest.raises(ValueError,match='reused an active agent'):
        asyncio.run(generate(cases(),tmp_path/'traces.json',None,
                    agent_factory=lambda:singleton,case_concurrency=3))
    assert len(list((tmp_path/'sessions').glob('*/turn_0.json'))) == 1


@pytest.mark.parametrize('value',[0,-1,True,1.5,'4'])
def test_invalid_concurrency_rejected_before_generation(tmp_path,value):
    with pytest.raises(ValueError,match='positive integer'):
        asyncio.run(generate(cases(),tmp_path/'traces.json',None,
                             agent_factory=create_agent,case_concurrency=value))
    assert not (tmp_path/'generation-started.json').exists()
