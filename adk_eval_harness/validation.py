"""Offline pack validation before model generation or native grading."""
import math
from pathlib import Path
import re

import yaml

from .gate import read

MULTI_METRICS = {'multi_turn_task_success', 'multi_turn_tool_use_quality',
                 'multi_turn_trajectory_quality'}
SINGLE_METRICS = {'general_quality', 'text_quality', 'instruction_following',
                  'final_response_quality', 'final_response_match',
                  'final_response_reference_free', 'tool_use_quality',
                  'grounding', 'hallucination', 'safety'}
OBSERVATION_KEYS = {'calls', 'results', 'forbidden', 'before', 'call_counts'}
CASE_KEYS = OBSERVATION_KEYS | {'turns', 'min_turns', 'json_response', 'json_response_expected'}
TURN_KEYS = OBSERVATION_KEYS | {'state_equals', 'json_response_expected'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def content_text(value, label):
    require(isinstance(value, dict), f'{label}: expected text Content object')
    require(set(value) <= {'role', 'parts'}, f'{label}: unsupported Content fields')
    parts = value.get('parts')
    require(isinstance(parts, list) and bool(parts), f'{label}: nonempty parts required')
    require(all(isinstance(p, dict) and set(p) == {'text'} and isinstance(p['text'], str)
                for p in parts), f'{label}: only text parts are supported')
    result = ''.join(p['text'] for p in parts)
    require(nonempty_text(result), f'{label}: empty text')
    return result


def validate_rules(rules, label, *, turn=False, extensions=()):
    require(isinstance(rules, dict), f'{label}: rules must be an object')
    unknown = set(rules) - (TURN_KEYS if turn else CASE_KEYS) - set(extensions)
    require(not unknown, f'{label}: unknown rule fields {sorted(unknown)}')
    for key, payload in [('calls', 'args'), ('results', 'values')]:
        if key in rules:
            items = rules[key]
            require(isinstance(items, list), f'{label}/{key}: expected list')
            for item in items:
                require(isinstance(item, dict) and set(item) <= {'name', payload}
                        and nonempty_text(item.get('name'))
                        and isinstance(item.get(payload, {}), dict),
                        f'{label}/{key}: expected name and optional {payload} object')
    if 'forbidden' in rules:
        require(isinstance(rules['forbidden'], list) and
                all(nonempty_text(x) for x in rules['forbidden']), f'{label}/forbidden: expected tool names')
    if 'before' in rules:
        require(isinstance(rules['before'], list) and all(
            isinstance(pair, list) and len(pair) == 2 and all(nonempty_text(x) for x in pair)
            for pair in rules['before']), f'{label}/before: expected pairs of tool names')
    if 'call_counts' in rules:
        counts = rules['call_counts']
        require(isinstance(counts, dict) and all(nonempty_text(k) and type(v) is int and v >= 0
                for k, v in counts.items()), f'{label}/call_counts: expected nonnegative integers')
    if 'min_turns' in rules:
        require(type(rules['min_turns']) is int and rules['min_turns'] > 0,
                f'{label}/min_turns: expected positive integer')
    if 'json_response' in rules:
        require(type(rules['json_response']) is bool, f'{label}/json_response: expected boolean')
    if 'state_equals' in rules:
        require(isinstance(rules['state_equals'], dict), f'{label}/state_equals: expected object')
    if 'turns' in rules:
        require(isinstance(rules['turns'], dict), f'{label}/turns: expected object')
        for index, value in rules['turns'].items():
            require(isinstance(index, str) and re.fullmatch(r'0|[1-9][0-9]*', index),
                    f'{label}/turns: expected canonical zero-based indices')
            validate_rules(value, f'{label}/turns/{index}', turn=True, extensions=extensions)


def validate_policy(policy, assertion_hook=None):
    """Validate gate semantics too; hook extensions are explicitly declared by trusted code."""
    require(isinstance(policy, dict), 'policy: expected object')
    require(type(policy.get('schema_version')) is int and policy['schema_version'] in (1, 2),
            'policy: unsupported schema_version')
    allowed = {'schema_version', 'thresholds', 'suites', 'metric_roles',
               'calibration_rationale', 'assertion_hook'}
    require(not set(policy) - allowed, f'policy: unknown fields {sorted(set(policy) - allowed)}')
    thresholds = policy.get('thresholds')
    require(isinstance(thresholds, dict) and bool(thresholds), 'policy: thresholds required')
    for metric, threshold in thresholds.items():
        require(nonempty_text(metric) and type(threshold) in (int, float)
                and math.isfinite(threshold) and 0 <= threshold <= 1,
                f'policy/{metric}: threshold must be finite in [0, 1]')
    roles = policy.get('metric_roles', {})
    require(isinstance(roles, dict) and all(k in thresholds and v in ('blocking', 'advisory')
            for k, v in roles.items()), 'policy: invalid metric roles')
    require('advisory' not in roles.values() or nonempty_text(policy.get('calibration_rationale')),
            'policy: advisory metrics require calibration_rationale')
    if assertion_hook is None and policy.get('assertion_hook'):
        from .config import symbol
        assertion_hook = symbol(policy['assertion_hook'])
    if assertion_hook is not None:
        require(callable(assertion_hook), 'policy: assertion_hook must be callable')
    extensions = getattr(assertion_hook, 'rule_keys', ())
    require(isinstance(extensions, (set, list, tuple)) and all(nonempty_text(x) for x in extensions),
            'policy: hook rule_keys must contain field names')
    require(not set(extensions) & (CASE_KEYS | TURN_KEYS), 'policy: hook rule_keys overlap generic rules')
    suites = policy.get('suites')
    require(isinstance(suites, dict) and bool(suites), 'policy: suites required')
    for name, suite in suites.items():
        require(isinstance(suite, dict) and set(suite) == {'metrics', 'cases'},
                f'policy/{name}: expected metrics and cases')
        metrics = suite['metrics']
        require(isinstance(metrics, list) and bool(metrics) and all(nonempty_text(x) for x in metrics)
                and len(metrics) == len(set(metrics)), f'policy/{name}: unique metrics required')
        require(set(metrics) <= set(thresholds), f'policy/{name}: missing metric threshold')
        require(isinstance(suite['cases'], dict) and bool(suite['cases']), f'policy/{name}: cases required')
        for cid, rules in suite['cases'].items():
            validate_rules(rules, f'policy/{name}/{cid}', extensions=extensions)


def dataset_cases(path):
    """Normalize only supported authored inputs; never silently ignore stored histories/traces."""
    data = read(Path(path))
    is_single = isinstance(data, dict)
    if is_single:
        native_cases = data.get('eval_cases')
        if isinstance(native_cases, list) and any(
                isinstance(c, dict) and ('eval_id' in c or 'conversation' in c) for c in native_cases):
            raise ValueError('dataset: native ADK EvalSet format is not supported directly; convert eval_id/conversation '
                             'to eval_case_id/messages and explicitly map session state and expected assertions')
        require(set(data) == {'eval_cases'}, 'dataset: expected eval_cases envelope')
        data = data['eval_cases']
    require(isinstance(data, list) and bool(data), 'dataset: nonempty case list required')
    cases, seen = [], set()
    for case in data:
        require(isinstance(case, dict), 'dataset: case must be an object')
        cid = case.get('eval_case_id')
        require(isinstance(cid, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', cid),
                'dataset: eval_case_id must be 1-128 ASCII letters, digits, underscores or hyphens')
        require(cid not in seen, f'dataset/{cid}: duplicate eval_case_id')
        seen.add(cid)
        allowed = {'eval_case_id', 'prompt', 'reference', 'context', 'rubric_groups'} | (set() if is_single else {'messages'})
        require(not set(case) - allowed, f'dataset/{cid}: unsupported fields {sorted(set(case) - allowed)}')
        prompt = content_text(case['prompt'], f'{cid}/prompt') if 'prompt' in case else None
        require('prompt' not in case or case['prompt'].get('role', 'user') == 'user',
                f'dataset/{cid}/prompt: user role required')
        messages = [prompt] if is_single else case.get('messages')
        require(isinstance(messages, list) and bool(messages) and all(nonempty_text(x) for x in messages),
                f'dataset/{cid}: nonempty text messages required')
        require(prompt is None or prompt == messages[0], f'dataset/{cid}: prompt differs from first message')
        if 'reference' in case:
            reference = case['reference']
            require(isinstance(reference, dict) and set(reference) == {'response'},
                    f'dataset/{cid}/reference: expected response wrapper')
            content_text(reference['response'], f'{cid}/reference/response')
        if 'context' in case:
            if isinstance(case['context'], dict):
                content_text(case['context'], f'{cid}/context')
            else:
                require(nonempty_text(case['context']), f'dataset/{cid}/context: expected source text')
        if 'rubric_groups' in case:
            groups = case['rubric_groups']
            require(isinstance(groups, dict) and len(groups) == 1,
                    f'dataset/{cid}/rubric_groups: exactly one group supported without a metric selector')
            for group in groups.values():
                require(isinstance(group, dict) and isinstance(group.get('rubrics'), list)
                        and bool(group['rubrics']), f'dataset/{cid}/rubric_groups: nonempty rubrics required')
                ids = []
                for rubric in group['rubrics']:
                    require(isinstance(rubric, dict) and nonempty_text(rubric.get('rubric_id')),
                            f'dataset/{cid}/rubric_groups: rubric_id required')
                    ids.append(rubric['rubric_id'])
                    content = rubric.get('content', {})
                    prop = content.get('property', {}) if isinstance(content, dict) else {}
                    require(isinstance(prop, dict) and nonempty_text(prop.get('description')),
                            f'dataset/{cid}/rubric_groups: property description required')
                require(len(ids) == len(set(ids)), f'dataset/{cid}/rubric_groups: duplicate rubric_id')
        cases.append({**case, 'messages': messages})
    return cases


def validate(config):
    """Check local contracts; cloud versions, availability and quotas require actual results."""
    policy = read(config['policy'])
    validate_policy(policy)
    require(set(config['suites']) == set(policy['suites']), 'config/policy suite coverage differs')
    plan = {'suite_count': len(config['suites']), 'case_count': 0, 'turn_count': 0,
            'native_grade_count': 0, 'per_suite': {}, 'metric_requests': {}}
    for name, spec in config['suites'].items():
        try:
            metric_config = yaml.safe_load(Path(spec['metrics']).read_text())
            require(isinstance(metric_config, dict) and set(metric_config) <= {'metrics_to_run', 'custom_metrics'},
                    'metrics: expected native metrics_to_run configuration')
            require(not metric_config.get('custom_metrics'), 'metrics: custom metrics are outside this native-only reference')
            metrics = metric_config.get('metrics_to_run')
            require(metrics == policy['suites'][name]['metrics'], 'config/policy metrics differ')
            # A catalog match establishes local recognition only. The CLI passes bare
            # names through; the service can emit a version other than the catalog latest.
            from google.agents.cli.eval.eval_utils import _resolve_predefined_metric_name
            requests = {}
            bases = set()
            for metric in metrics:
                base = re.sub(r'_v[0-9]+$', '', metric)
                require(base in SINGLE_METRICS | MULTI_METRICS,
                        f'metrics: {metric} is not supported by this text/agent-data adapter')
                require(base not in bases, f'metrics: duplicate base metric {base}; select one version per suite')
                bases.add(base)
                catalog_match = _resolve_predefined_metric_name(metric)
                require(catalog_match is not None,
                        f'metrics: {metric} is unavailable in the pinned CLI/SDK metric catalog')
                requests[metric] = {
                    'catalog_match': catalog_match,
                    'version_selection': 'explicit' if re.search(r'_v[0-9]+$', metric) else 'service_default',
                    'observed_native_metric': None,
                }
            plan['metric_requests'][name] = requests
            cases = dataset_cases(spec['dataset'])
            rules = policy['suites'][name]['cases']
            require({c['eval_case_id'] for c in cases} == set(rules), 'dataset/policy case coverage differs')
            for case in cases:
                cid = case['eval_case_id']
                turns = len(case['messages'])
                required = rules[cid]
                require(required.get('min_turns', 1) <= turns and all(int(i) < turns for i in required.get('turns', {})),
                        f'{cid}: policy refers to absent turns')
                for metric in metrics:
                    base = re.sub(r'_v[0-9]+$', '', metric)
                    require(turns == 1 or base in MULTI_METRICS, f'{cid}/{metric}: metric rejects multi-turn agent traces')
                    for metric_name, field in [('final_response_match', 'reference'), ('grounding', 'context'),
                                               ('final_response_reference_free', 'rubric_groups')]:
                        require(base != metric_name or field in case, f'{cid}/{metric}: missing required {field}')
            counts = {'case_count': len(cases), 'turn_count': sum(len(c['messages']) for c in cases),
                      'native_grade_count': len(cases) * len(metrics)}
            plan['per_suite'][name] = counts
            for key, value in counts.items():
                plan[key] += value
        except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError) as exc:
            raise ValueError(f'suite {name}: {exc}') from exc
    return plan
