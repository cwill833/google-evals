"""Reject packs that would otherwise spend model calls before discovering bad inputs."""
import copy
import json
from pathlib import Path

import pytest

from adk_eval_harness.config import load
from adk_eval_harness.validation import dataset_cases, validate, validate_policy

ROOT = Path(__file__).resolve().parents[1]


def pack(tmp_path, metric='final_response_match', messages=None):
    case = {'eval_case_id': 'example', 'prompt': {'role': 'user', 'parts': [{'text': 'Hello'}]},
            'reference': {'response': {'parts': [{'text': 'Hi'}]}}}
    data = {'eval_cases': [case]}
    if messages is not None:
        case.pop('prompt')
        case['messages'] = messages
        data = [case]
    policy = {'schema_version': 2, 'thresholds': {metric: 0.8},
              'suites': {'sample': {'metrics': [metric], 'cases': {'example': {}}}}}
    dataset = tmp_path / 'cases.json'
    dataset.write_text(json.dumps(data))
    policy_path = tmp_path / 'policy.json'
    policy_path.write_text(json.dumps(policy))
    metrics = tmp_path / 'metrics.yaml'
    metrics.write_text('metrics_to_run:\n  - ' + metric + '\n')
    config = {'policy': policy_path, 'suites': {'sample': {'dataset': dataset, 'metrics': metrics}}}
    return config, policy, data


@pytest.mark.parametrize('name,filename,cases,grades', [
    ('finance', 'config.json', 28, 56), ('support', 'config.json', 8, 20),
    ('finance', 'fault-config.json', 4, 4), ('support', 'fault-config.json', 2, 6)])
def test_existing_packs_validate_without_changing_inputs(name, filename, cases, grades):
    plan = validate(load(ROOT / 'packs' / name / filename))
    assert plan['case_count'] == cases
    assert plan['native_grade_count'] == grades


@pytest.mark.parametrize('metric,field', [('final_response_match', 'reference'),
                                        ('grounding', 'context'),
                                        ('final_response_reference_free', 'rubric_groups')])
def test_required_metric_inputs_fail_before_execution(tmp_path, metric, field):
    config, _, data = pack(tmp_path, metric)
    data['eval_cases'][0].pop(field, None)
    config['suites']['sample']['dataset'].write_text(json.dumps(data))
    with pytest.raises(ValueError, match=f'missing required {field}'):
        validate(config)


def test_multiturn_cannot_silently_use_single_turn_grader(tmp_path):
    config, _, _ = pack(tmp_path, messages=['Hello', 'And another thing'])
    with pytest.raises(ValueError, match='metric rejects multi-turn'):
        validate(config)


@pytest.mark.parametrize('metric', ['made_up_metric', 'gecko_text2image', 'multi_turn_general_quality'])
def test_unsupported_metric_representations_fail_early(tmp_path, metric):
    config, _, _ = pack(tmp_path, metric)
    with pytest.raises(ValueError, match='not supported by this text/agent-data adapter'):
        validate(config)


@pytest.mark.parametrize('mutation,message', [
    (lambda c: c.update(forbidden_tools=['write']), 'unknown rule fields'),
    (lambda c: c.update(turns={'01': {}}), 'canonical zero-based'),
    (lambda c: c.update(call_counts={'write': True}), 'nonnegative integers'),
    (lambda c: c.update(calls=[{'name': 'write', 'args': []}]), 'args object'),
    (lambda c: c.update(before=[['one']]), 'pairs of tool names')])
def test_policy_typos_and_malformed_assertions_do_not_disappear(tmp_path, mutation, message):
    _, policy, _ = pack(tmp_path)
    mutation(policy['suites']['sample']['cases']['example'])
    with pytest.raises(ValueError, match=message):
        validate_policy(policy)


def test_hook_extensions_are_explicit_and_cannot_replace_generic_rules(tmp_path):
    _, policy, _ = pack(tmp_path)
    policy['suites']['sample']['cases']['example']['custom_state'] = {'done': True}
    hook = lambda case, rules: []
    with pytest.raises(ValueError, match='unknown rule fields'):
        validate_policy(policy, hook)
    hook.rule_keys = {'custom_state'}
    validate_policy(policy, hook)
    hook.rule_keys = {'calls'}
    with pytest.raises(ValueError, match='overlap'):
        validate_policy(policy, hook)


@pytest.mark.parametrize('mutation,message', [
    (lambda data: data['eval_cases'].append(copy.deepcopy(data['eval_cases'][0])), 'duplicate eval_case_id'),
    (lambda data: data['eval_cases'][0].update(agent_data={'turns': []}), 'unsupported fields'),
    (lambda data: data['eval_cases'][0]['prompt']['parts'].append({'inline_data': {}}), 'only text parts'),
    (lambda data: data['eval_cases'][0].update(eval_case_id='../escape'), 'eval_case_id')])
def test_dataset_lossy_or_unsafe_inputs_fail_early(tmp_path, mutation, message):
    config, _, data = pack(tmp_path)
    mutation(data)
    path = config['suites']['sample']['dataset']
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=message):
        dataset_cases(path)


def test_all_suites_are_validated_and_plan_counts_actual_turns(tmp_path):
    config, policy, _ = pack(tmp_path, 'multi_turn_task_success', ['one', 'two', 'three'])
    assert validate(config)['turn_count'] == 3
    policy['suites']['sample']['cases']['example']['turns'] = {'3': {'forbidden': ['write']}}
    config['policy'].write_text(json.dumps(policy))
    with pytest.raises(ValueError, match='absent turns'):
        validate(config)


def test_advisory_roles_still_require_threshold_and_rationale(tmp_path):
    _, policy, _ = pack(tmp_path)
    policy['metric_roles'] = {'final_response_match': 'advisory'}
    with pytest.raises(ValueError, match='calibration_rationale'):
        validate_policy(policy)


def test_native_adk_export_gets_actionable_conversion_error(tmp_path):
    path = tmp_path / 'native.json'
    path.write_text(json.dumps({'eval_set_id': 'existing', 'eval_cases': [
        {'eval_id': 'case-1', 'conversation': [], 'session_input': {}}]}))
    with pytest.raises(ValueError, match='convert eval_id/conversation'):
        dataset_cases(path)


def test_uuid_style_id_is_supported_without_path_segments(tmp_path):
    config, _, data = pack(tmp_path)
    data['eval_cases'][0]['eval_case_id'] = 'c3f4b8e7-35e5-4732-a2ca-6113cbf404a8'
    path = config['suites']['sample']['dataset']
    path.write_text(json.dumps(data))
    assert dataset_cases(path)[0]['eval_case_id'].startswith('c3f4b8e7-')


def test_metric_versions_use_pinned_cli_catalog(tmp_path):
    from google.agents.cli.eval.eval_utils import _resolve_predefined_metric_name
    exact = _resolve_predefined_metric_name('final_response_match')
    config, _, _ = pack(tmp_path, exact)
    plan = validate(config)
    assert plan['metric_requests']['sample'] == {exact: {
        'catalog_match': exact, 'version_selection': 'explicit', 'observed_native_metric': None}}


def test_bare_metric_catalog_match_does_not_claim_effective_cloud_version(tmp_path):
    from google.agents.cli.eval.eval_utils import _resolve_predefined_metric_name, prepare_eval_metrics
    config, _, _ = pack(tmp_path)
    plan = validate(config)
    prepared, local, remote = prepare_eval_metrics(str(config['suites']['sample']['metrics']), None)
    assert prepared == ['final_response_match']  # The pinned CLI does not pin to its local catalog candidate.
    assert (local, remote) == (0, 0)
    assert 'resolved_native_metrics' not in plan
    assert plan['metric_requests']['sample']['final_response_match'] == {
        'catalog_match': _resolve_predefined_metric_name('final_response_match'),
        'version_selection': 'service_default',
        'observed_native_metric': None,
    }


def test_unavailable_metric_version_rejected_offline(tmp_path):
    config, _, _ = pack(tmp_path, 'final_response_match_v999')
    with pytest.raises(ValueError, match='unavailable in the pinned CLI/SDK'):
        validate(config)


def test_versioned_metric_still_requires_reference(tmp_path):
    from google.agents.cli.eval.eval_utils import _resolve_predefined_metric_name
    config, _, data = pack(tmp_path, _resolve_predefined_metric_name('final_response_match'))
    data['eval_cases'][0].pop('reference')
    config['suites']['sample']['dataset'].write_text(json.dumps(data))
    with pytest.raises(ValueError, match='missing required reference'):
        validate(config)


def test_versioned_metric_still_rejects_multiturn_input(tmp_path):
    from google.agents.cli.eval.eval_utils import _resolve_predefined_metric_name
    config, _, _ = pack(tmp_path, _resolve_predefined_metric_name('final_response_match'), ['one', 'two'])
    with pytest.raises(ValueError, match='metric rejects multi-turn'):
        validate(config)


def test_bare_and_versioned_aliases_cannot_duplicate_a_metric(tmp_path):
    from google.agents.cli.eval.eval_utils import _resolve_predefined_metric_name
    exact = _resolve_predefined_metric_name('final_response_match')
    config, policy, _ = pack(tmp_path)
    policy['thresholds'][exact] = 0.8
    policy['suites']['sample']['metrics'].append(exact)
    config['policy'].write_text(json.dumps(policy))
    config['suites']['sample']['metrics'].write_text('metrics_to_run:\n  - final_response_match\n  - ' + exact + '\n')
    with pytest.raises(ValueError, match='duplicate base metric'):
        validate(config)
