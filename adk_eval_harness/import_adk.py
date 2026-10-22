"""Explicit, reviewable conversion of static text ADK EvalSets into draft packs.

This is a format adapter, not an assertion that native ADK grading and managed
Google Agents CLI grading have identical semantics. No agents or graders run.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from google.adk.evaluation.eval_set import EvalSet

from .gate import digest, read

METRIC = 'multi_turn_task_success'
SLUG = re.compile(r'[A-Za-z0-9_-]{1,128}')


def known_fields(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f'{label}: unsupported fields: {sorted(unknown)}')


def text_content(value, label, role):
    known_fields(value, {'parts', 'role'}, label)
    if value.get('role') not in (None, role):
        raise ValueError(f'{label}: expected {role} role')
    parts = value.get('parts')
    if not isinstance(parts, list) or not parts:
        raise ValueError(f'{label}: text parts are required')
    for part in parts:
        known_fields(part, {'text'}, label + '.part')
        if not isinstance(part.get('text'), str):
            raise ValueError(f'{label}: only plain text is supported')
    text = ''.join(part['text'] for part in parts)
    if not text.strip():
        raise ValueError(f'{label}: empty text is unsupported')
    return text


def convert(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    raw = read(source)
    known_fields(raw, EvalSet.model_fields, 'EvalSet')
    # Pinned ADK validates the actual exported schema before any output is written.
    EvalSet.model_validate(raw)
    cases, rules, references, sessions = [], {}, {}, []
    for case in raw.get('eval_cases', []):
        known_fields(case, {'eval_id', 'conversation', 'conversation_scenario',
                           'session_input', 'creation_timestamp', 'rubrics',
                           'final_session_state'}, 'case')
        cid = case['eval_id']
        if not isinstance(cid, str) or not SLUG.fullmatch(cid) or cid in rules:
            raise ValueError('Case IDs must be unique safe slugs of 1–128 characters')
        if case.get('conversation_scenario') is not None:
            raise ValueError(f'{cid}: dynamic conversation scenarios are unsupported')
        if case.get('rubrics'):
            raise ValueError(f'{cid}: custom ADK rubrics need manual native metric mapping')
        if 'final_session_state' in case and case['final_session_state'] is not None:
            raise ValueError(f'{cid}: final_session_state requires manual snapshot assertions')
        session = case.get('session_input') or {}
        known_fields(session, {'app_name', 'user_id', 'session_id', 'state'}, cid + '.session_input')
        if session.get('session_id'):
            raise ValueError(f'{cid}: fixed session IDs/artifact reuse are unsupported')
        sessions.append({'app_name': session.get('app_name', 'imported_adk_evaluation'),
                         'user_id': session.get('user_id', 'evaluation_user'),
                         'initial_state': session.get('state', {})})
        invocations = case.get('conversation')
        if not isinstance(invocations, list) or not invocations:
            raise ValueError(f'{cid}: a nonempty static conversation is required')
        messages, turn_rules, expected_replies = [], {}, []
        for index, invocation in enumerate(invocations):
            label = f'{cid}/turn_{index}'
            known_fields(invocation, {'invocation_id', 'user_content', 'final_response',
                                     'intermediate_data', 'creation_timestamp', 'duration',
                                     'rubrics', 'app_details'}, label)
            if invocation.get('rubrics'):
                raise ValueError(f'{label}: custom ADK rubrics need manual native metric mapping')
            messages.append(text_content(invocation['user_content'], label + '.user_content', 'user'))
            expected = invocation.get('final_response')
            expected_replies.append(text_content(expected, label + '.final_response', 'model') if expected else None)
            intermediate = invocation.get('intermediate_data') or {}
            known_fields(intermediate, {'tool_uses', 'tool_responses', 'intermediate_responses'}, label + '.intermediate_data')
            if intermediate.get('intermediate_responses'):
                raise ValueError(f'{label}: intermediate agent responses need manual mapping')
            calls, results = [], []
            for call in intermediate.get('tool_uses') or []:
                known_fields(call, {'id', 'name', 'args'}, label + '.tool_use')
                if not call.get('name') or not isinstance(call.get('args', {}), dict):
                    raise ValueError(f'{label}: malformed tool call')
                calls.append({'name': call['name'], 'args': call.get('args') or {}})
            for response in intermediate.get('tool_responses') or []:
                known_fields(response, {'id', 'name', 'response'}, label + '.tool_response')
                if not response.get('name') or not isinstance(response.get('response'), dict):
                    raise ValueError(f'{label}: malformed tool response')
                results.append({'name': response['name'], 'values': response['response']})
            if calls or results:
                turn_rules[str(index)] = {'calls': calls, 'results': results,
                                          'call_counts': dict(Counter(c['name'] for c in calls))}
        dataset_case = {'eval_case_id': cid, 'messages': messages}
        if expected_replies[-1] is not None:
            dataset_case['reference'] = {'response': {'role': 'model', 'parts': [{'text': expected_replies[-1]}]}}
        cases.append(dataset_case)
        rules[cid] = {'min_turns': len(messages), 'turns': turn_rules}
        references[cid] = expected_replies
    if not cases:
        raise ValueError('EvalSet must contain at least one case')
    if any(session != sessions[0] for session in sessions[1:]):
        raise ValueError('Per-case session inputs differ; split into packs sharing app_name, user_id and initial_state')
    warnings = [
        'DRAFT: review requirements, metric choice and threshold before using this pack as a release gate.',
        'All user turns are replayed against the live agent. Expected assistant replies are never seeded into its session.',
        'Only the last expected assistant reply becomes a native reference; earlier replies remain in expected-responses.json and are not individually scored.',
        'Tool assertions require listed argument/result subsets and per-name call counts; they do not enforce exact argument objects or original tool ordering.',
        'Empty tool_uses does not prohibit tool calls, extra tool names are unconstrained, and expected calls/results are not paired by their original IDs; add explicit requirements before gating these behaviors.',
        'ADK invocation metadata (timing, app_details, invocation IDs) remains in source.evalset.json, not in scoring inputs.',
        'This conversion changes evaluator semantics: it does not reproduce native ADK metrics or any prior ADK score.',
    ]
    report = {'schema_version': 1, 'draft_review_required': True,
              'source_sha256': digest(source), 'case_count': len(cases),
              'turn_count': sum(len(c['messages']) for c in cases), 'warnings': warnings}
    policy = {'schema_version': 2, 'thresholds': {METRIC: 0.8},
              'suites': {'imported': {'metrics': [METRIC], 'cases': rules}}}
    fragment = {**sessions[0], 'suites': {'imported': {'dataset': 'dataset.json', 'metrics': 'metrics.yaml'}},
                'policy': 'policy.json'}
    # New directory only: conversion cannot overwrite reviewed expectations.
    output.mkdir(parents=True, exist_ok=False)
    for name, value in [('dataset.json', cases), ('policy.json', policy),
                        ('expected-responses.json', references), ('config.fragment.json', fragment),
                        ('conversion-report.json', report)]:
        (output / name).write_text(json.dumps(value, indent=2) + '\n')
    (output / 'source.evalset.json').write_bytes(source.read_bytes())
    (output / 'metrics.yaml').write_text('metrics_to_run:\n  - ' + METRIC + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = convert(args.source, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'Import failed: {exc}\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
