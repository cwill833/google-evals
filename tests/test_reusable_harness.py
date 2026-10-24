"""ADK-only portability and fail-closed policy checks over executed agents and synthetic policy fixtures."""
import asyncio
import copy
import json
from pathlib import Path
import pytest
from adk_eval_harness.config import load, symbol
from adk_eval_harness.gate import evaluate, digest, trace_checks
from adk_eval_harness.generation import generate
from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.genai import types
from tests.fixture_runs import make_run

ROOT = Path(__file__).resolve().parents[1]


class CounterAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        count = ctx.session.state.get('user:count', 0) + 1
        yield Event(author=self.name, actions=EventActions(state_delta={'user:count': count}),
                    content=types.Content(role='model', parts=[types.Part(text=str(count))]))


def test_runtime_isolates_user_state_as_well_as_session_state(tmp_path):
    out = tmp_path / 'traces.json'
    asyncio.run(generate([{'eval_case_id':'one','messages':['hi','again']},
                          {'eval_case_id':'two','messages':['hi']}], out, CounterAgent(name='counter'),
                         app_name='external_application', user_id='some_other_user'))
    cases = json.loads(out.read_text())['eval_cases']
    assert cases[0]['responses'][0]['response']['parts'][0]['text'] == '2'
    assert cases[1]['responses'][0]['response']['parts'][0]['text'] == '1'
    assert not trace_checks(cases[0], {'turns':{'1':{'state_equals':{'user:count':2}}}})


def test_external_project_loads_without_core_changes(tmp_path):
    project = tmp_path / 'other_project'
    project.mkdir()
    (project/'unrelated_adk.py').write_text('from google.adk.agents import BaseAgent\nroot_agent = BaseAgent(name="external")\n')
    policy = {'schema_version':2,'thresholds':{'final_response_match':1},'suites':{}}
    (project/'policy.json').write_text(json.dumps(policy))
    config = {'schema_version':1,'framework':'adk','project_root':'.','agent':'unrelated_adk:root_agent',
              'app_name':'external','user_id':'test','source_roots':['unrelated_adk.py'],
              'policy':'policy.json','suites':{}}
    path=project/'config.json';path.write_text(json.dumps(config))
    loaded=load(path)
    assert symbol(loaded['agent']).name == 'external'
    config['framework']='non_adk';path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='ADK'): load(path)


@pytest.mark.parametrize('corrupt', [False, True])
def test_advisory_scores_do_not_hide_evaluator_errors(tmp_path, corrupt):
    run=make_run(tmp_path, suites={'tools': ['final_response_match'],
        'multi_turn': ['multi_turn_tool_use_quality', 'multi_turn_trajectory_quality']},
        low_metrics=['multi_turn_tool_use_quality', 'multi_turn_trajectory_quality'])
    policy=json.loads((run/'policy.json').read_text())
    policy.update(schema_version=2,assertion_hook='examples.finance.assertions:check',
                  metric_roles={'multi_turn_tool_use_quality':'advisory','multi_turn_trajectory_quality':'advisory'},
                  calibration_rationale='Test of explicit reviewed role assignment')
    (run/'policy.json').write_text(json.dumps(policy))
    manifest=json.loads((run/'manifest.json').read_text());manifest['policy_sha256']=digest(run/'policy.json')
    if corrupt:
        entry=manifest['suites']['multi_turn'];path=run/entry['results'];data=json.loads(path.read_text())
        row=data['eval_case_results'][0]['response_candidate_results'][0]['metric_results']
        next(m for n,m in row.items() if n.startswith('multi_turn_tool_use_quality'))['error_message']='unavailable'
        path.write_text(json.dumps(data));entry['results_sha256']=digest(path)
    (run/'manifest.json').write_text(json.dumps(manifest))
    report=evaluate(run,run/'policy.json')
    assert report['passed'] is (not corrupt)
    assert report['evaluation_complete'] is (not corrupt)
    assert len(report['warnings']) == (3 if corrupt else 4)


def test_unsupported_role_and_missing_hook_fail_closed(tmp_path):
    run=make_run(tmp_path, suites={'tools': ['final_response_match'],
        'multi_turn': ['multi_turn_tool_use_quality', 'multi_turn_trajectory_quality']},
        low_metrics=['multi_turn_tool_use_quality', 'multi_turn_trajectory_quality'])
    policy=json.loads((run/'policy.json').read_text())
    policy['assertion_hook']='does_not_exist:check'
    (run/'policy.json').write_text(json.dumps(policy))
    assert not evaluate(run,run/'policy.json')['passed']


def test_frozen_config_and_policy_are_verified():
    for name in ['finance','support']:
        config=load(ROOT/f'packs/{name}/config.json')
        assert config['framework']=='adk'


def test_support_closure_guard_uses_current_turn_only():
    from examples.support.assertions import check
    case={'agent_data':{'turns':[{'events':[{'author':'user','content':{'parts':[{'text':'Cancel that request.'}]}},
            {'content':{'parts':[{'function_call':{'name':'close_ticket','args':{'ticket_id':'T101'}}}]}}]}]}}
    assert check(case,{}) == ['CRITICAL: unauthorized ticket closure attempt']


def test_broken_tool_trace_is_not_successful_negative_detection(tmp_path):
    from adk_eval_harness.gate import comparable
    run=make_run(tmp_path, suites={'tools': ['final_response_match'],
        'multi_turn': ['multi_turn_tool_use_quality', 'multi_turn_trajectory_quality']},
        low_metrics=['multi_turn_tool_use_quality', 'multi_turn_trajectory_quality'])
    manifest=json.loads((run/'manifest.json').read_text());entry=manifest['suites']['tools']
    trace_path=run/entry['trace'];traces=json.loads(trace_path.read_text())
    # Both copies are altered to test structural validity independently of hash binding.
    case=traces['eval_cases'][0]
    for turn in case['agent_data']['turns']:
        for event in turn['events']:
            for part in (event.get('content') or {}).get('parts',[]):
                if part.get('function_response'): part['function_response']['id']='unmatched'
    trace_path.write_text(json.dumps(traces));entry['trace_sha256']=digest(trace_path)
    result_path=run/entry['results'];result=json.loads(result_path.read_text())
    result['evaluation_dataset'][0]['eval_cases']=traces['eval_cases']
    result_path.write_text(json.dumps(result));entry['results_sha256']=digest(result_path)
    (run/'manifest.json').write_text(json.dumps(manifest))
    report=evaluate(run,run/'policy.json')
    assert not report['passed']
    assert not report['evaluation_complete']
