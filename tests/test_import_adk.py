"""Static ADK EvalSet import preserves expectations and rejects semantic losses."""
import copy
import json

import pytest

from adk_eval_harness.import_adk import convert
from adk_eval_harness.gate import digest, read
from adk_eval_harness.config import load
from adk_eval_harness.validation import validate


def text(value, role):
    return {'role': role, 'parts': [{'text': value}]}


def evalset():
    return {'eval_set_id': 'external-set', 'name': 'Exported from ADK', 'eval_cases': [{
        'eval_id': 'case-001',
        'session_input': {'app_name': 'external', 'user_id': 'tester', 'state': {'region': 'EU'}},
        'conversation': [
            {'invocation_id': 'i1', 'user_content': text('Read order 42', 'user'),
             'final_response': text('Order 42 is pending', 'model'),
             'intermediate_data': {'tool_uses': [{'name': 'get_order', 'args': {'order_id': '42'}}],
                                   'tool_responses': [{'name': 'get_order', 'response': {'status': 'pending'}}]}},
            {'invocation_id': 'i2', 'user_content': text('Leave it unchanged', 'user'),
             'final_response': text('The order is unchanged', 'model')},
        ],
    }]}


def write(tmp_path, value):
    path = tmp_path / 'original.test.json'
    path.write_text(json.dumps(value))
    return path


def test_import_preserves_user_turns_references_tools_and_shared_state(tmp_path):
    source = write(tmp_path, evalset())
    output = tmp_path / 'imported'
    report = convert(source, output)
    assert report['draft_review_required'] is True
    assert report['source_sha256'] == digest(source) == digest(output / 'source.evalset.json')
    warnings = ' '.join(report['warnings'])
    assert 'Empty tool_uses does not prohibit tool calls' in warnings
    assert 'extra tool names are unconstrained' in warnings
    assert 'not paired by their original IDs' in warnings
    dataset = read(output / 'dataset.json')
    assert dataset == [{'eval_case_id': 'case-001', 'messages': ['Read order 42', 'Leave it unchanged'],
                        'reference': {'response': text('The order is unchanged', 'model')}}]
    assert read(output / 'expected-responses.json')['case-001'] == ['Order 42 is pending', 'The order is unchanged']
    fragment = read(output / 'config.fragment.json')
    assert fragment['initial_state'] == {'region': 'EU'}
    assert fragment['user_id'] == 'tester'
    rules = read(output / 'policy.json')['suites']['imported']['cases']['case-001']
    assert rules['turns']['0']['calls'] == [{'name': 'get_order', 'args': {'order_id': '42'}}]
    assert rules['turns']['0']['call_counts'] == {'get_order': 1}
    assert rules['turns']['0']['results'] == [{'name': 'get_order', 'values': {'status': 'pending'}}]
    with pytest.raises(FileExistsError):
        convert(source, output)


@pytest.mark.parametrize('failure', ['mixed_state', 'fixed_session', 'scenario', 'media',
                                    'rubrics', 'final_state', 'empty_final_state', 'events', 'intermediate_reply',
                                    'unknown_requirement', 'duplicate_id'])
def test_import_rejects_unsupported_semantics_without_partial_pack(tmp_path, failure):
    data = evalset()
    case = data['eval_cases'][0]
    invocation = case['conversation'][0]
    if failure == 'mixed_state':
        other = copy.deepcopy(case)
        other['eval_id'] = 'case-002'
        other['session_input']['state']['region'] = 'US'
        data['eval_cases'].append(other)
    elif failure == 'fixed_session':
        case['session_input']['session_id'] = 'preloaded'
    elif failure == 'scenario':
        case.pop('conversation')
        case['conversation_scenario'] = {'starting_prompt': 'hello', 'conversation_plan': 'ask followups'}
    elif failure == 'media':
        invocation['user_content']['parts'] = [{'inline_data': {'mime_type': 'image/png', 'data': 'YWJj'}}]
    elif failure == 'rubrics':
        case['rubrics'] = [{'rubric_id': 'style', 'rubric_content': {'text_property': 'Be concise'}}]
    elif failure == 'final_state':
        case['final_session_state'] = {'region': 'EU'}
    elif failure == 'empty_final_state':
        case['final_session_state'] = {}
    elif failure == 'events':
        invocation['intermediate_data'] = {'invocation_events': []}
    elif failure == 'intermediate_reply':
        invocation['intermediate_data']['intermediate_responses'] = [['helper', [{'text': 'work'}]]]
    elif failure == 'unknown_requirement':
        case['must_never'] = ['cancel_order']
    elif failure == 'duplicate_id':
        data['eval_cases'].append(copy.deepcopy(case))
    output = tmp_path / 'imported'
    with pytest.raises(ValueError):
        convert(write(tmp_path, data), output)
    assert not output.exists()


def test_imported_pack_passes_offline_preflight_with_project_config(tmp_path):
    output = tmp_path / 'imported'
    convert(write(tmp_path, evalset()), output)
    (tmp_path / 'external_agent.py').write_text('root_agent = None\n')
    config = {**read(output / 'config.fragment.json'),
              'schema_version': 1, 'framework': 'adk', 'project_root': '..',
              'agent': 'external_agent:root_agent', 'source_roots': ['external_agent.py']}
    path = output / 'config.json'
    path.write_text(json.dumps(config))
    plan = validate(load(path))
    assert plan['case_count'] == 1
    assert plan['turn_count'] == 2
    assert plan['native_grade_count'] == 1
