"""Test the real persistent-session adapter and gate with deterministic ADK events."""
import asyncio
import json
import copy
from pathlib import Path
import pytest
from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.adk.tools import ToolContext
from google.genai import types
from agent.tools import capture_authorization, create_transfer, send_notification, get_account_balance
from scripts.generate_conversations import generate
from scripts.quality_gate import trace_checks
from scripts.run_evals import verify_holdout


class StatefulDriver(BaseAgent):
    async def _run_async_impl(self, ctx):
        tc = ToolContext(ctx)
        capture_authorization(tc)
        message = ''.join(p.text or '' for p in ctx.user_content.parts)
        if message.startswith('I authorize'):
            args = {'source':'A100','destination':'A200','amount_cents':40000}
            tasks = [('create_transfer', args, create_transfer)]
        else:
            tasks = [('get_account_balance', {'account_id':'A100'}, get_account_balance)]
        for name, args, function in tasks:
            yield Event(author=self.name, content=types.Content(role='model', parts=[
                types.Part(function_call=types.FunctionCall(id='test_call', name=name, args=args))]))
            result = function(**args, tool_context=tc)
            yield Event(author=self.name, actions=tc.actions, content=types.Content(role='user', parts=[
                types.Part(function_response=types.FunctionResponse(id='test_call', name=name, response=result))]))
        yield Event(author=self.name, content=types.Content(role='model', parts=[types.Part(text=json.dumps(result))]))


def test_export_retains_session_state_and_isolates_cases(tmp_path):
    approval = 'I authorize a simulated transfer of 40000 cents from A100 to A200.'
    output = tmp_path / 'traces.json'
    asyncio.run(generate([
        {'eval_case_id':'first','messages':[approval, approval, 'Read balance']},
        {'eval_case_id':'isolated','messages':['Read balance']},
    ], output, StatefulDriver(name='driver')))
    cases = json.loads(output.read_text())['eval_cases']
    rules = {'min_turns':3,'turns':{
        '1':{'results':[{'name':'create_transfer','values':{'error':'DAILY_LIMIT_EXCEEDED'}}],
             'ledger':{'transfer_count':1,'daily_total_cents':40000}},
        '2':{'results':[{'name':'get_account_balance','values':{'balance_cents':85050}}]}}}
    assert trace_checks(cases[0], rules) == []
    assert trace_checks(cases[1], {'results':[{'name':'get_account_balance','values':{'balance_cents':125050}}]}) == []
    snapshots = [json.loads(p.read_text()) for p in (tmp_path / 'sessions/first').glob('state_*.json')]
    assert len({s['session_id'] for s in snapshots}) == 1
    assert snapshots[-1]['state']['transfer_authorization'] is None
    # Moving an expected result to another turn must not satisfy a turn-specific assertion.
    wrong = copy.deepcopy(cases[0])
    wrong['agent_data']['turns'][1]['events'], wrong['agent_data']['turns'][0]['events'] = (
        wrong['agent_data']['turns'][0]['events'], wrong['agent_data']['turns'][1]['events'])
    assert trace_checks(wrong, rules)
    for turn in cases[0]['agent_data']['turns']:
        for event in turn['events']:
            event.pop('state_delta', None)
    assert any('ledger' in e for e in trace_checks(cases[0], rules))


class StalledDriver(BaseAgent):
    async def _run_async_impl(self, ctx):
        yield Event(author=self.name, content=types.Content(role='model', parts=[types.Part(text='partial')]), partial=True)
        await asyncio.sleep(10)


def test_failed_turn_preserves_partial_events_without_complete_trace(tmp_path):
    output = tmp_path / 'traces.json'
    with pytest.raises(TimeoutError):
        asyncio.run(generate([{'eval_case_id':'slow','messages':['hello']}], output,
                             StalledDriver(name='slow'), turn_timeout=0.05))
    assert not output.exists()
    assert json.loads((tmp_path / 'sessions/slow/turn_0.json').read_text())['events']


def test_holdout_rules_cannot_be_silently_weakened():
    policy = json.loads(Path('tests/eval/policies/release.json').read_text())
    verify_holdout(policy)
    policy['thresholds']['multi_turn_task_success'] = 0.1
    with pytest.raises(ValueError, match='frozen policy'):
        verify_holdout(policy)
