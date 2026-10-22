"""Small synthetic native-shaped runs for offline contracts, never quality evidence.

Tests create these in temporary directories. Historical Google runs stay outside Git
and can be checked separately with linux_verify_hardening.py --replay --evidence-root.
"""
import copy

from adk_eval_harness.gate import digest
from adk_eval_harness.runner import VERSIONS, save


def case(case_id, turns=2):
    reply = {'role': 'model', 'parts': [{'text': '{"status":"open"}'}]}
    user = {'role': 'user', 'parts': [{'text': 'Inspect the fixture record.'}]}
    events = []
    for index in range(turns):
        call_id = f'call-{index}'
        events.append({'turn_index': index, 'events': [
            {'author': 'user', 'content': copy.deepcopy(user)},
            {'author': 'fixture', 'content': {'parts': [{'function_call': {
                'id': call_id, 'name': 'inspect', 'args': {'id': case_id}}}]}},
            {'author': 'fixture', 'content': {'parts': [{'function_response': {
                'id': call_id, 'name': 'inspect', 'response': {'status': 'open'}}}]}},
            {'author': 'fixture', 'content': copy.deepcopy(reply)}]})
    return {'eval_case_id': case_id, 'prompt': user, 'responses': [{'response': reply}],
            'agent_data': {'turns': events}}


def make_run(tmp_path, *, suites=None, count=2, low_metrics=()):
    """Generate a legacy-format run; all scores/usage are explicitly synthetic."""
    suites = suites or {'single': ['final_response_match'], 'multi_turn': ['multi_turn_task_success']}
    run = tmp_path / 'run'
    policy = {'schema_version': 2, 'thresholds': {m: 1.0 for ms in suites.values() for m in ms}, 'suites': {}}
    manifest = {'schema_version': 2, 'agent': 'synthetic_fixture:agent', 'versions': VERSIONS,
                'commands': [{'exit_code': 0, 'fixture_provenance': 'synthetic'}], 'errors': [], 'suites': {}}
    for suite, metrics in suites.items():
        cases = [case(f'case-{i}') for i in range(count)]
        trace = {'eval_cases': cases}
        policy['suites'][suite] = {'metrics': metrics, 'cases': {c['eval_case_id']: {} for c in cases}}
        rows = []
        for index, c in enumerate(cases):
            payloads = {}
            for metric in metrics:
                native_name = metric if metric.endswith(('_v1', '_v2', '_v3')) else metric + '_v2'
                payloads[native_name] = {'metric_name': native_name, 'score': .5 if metric in low_metrics else 1.0,
                                        'explanation': 'SYNTHETIC fixture; not a Google quality judgment.'}
            rows.append({'eval_case_index': index, 'response_candidate_results': [
                {'response_index': 0, 'metric_results': payloads}]})
            for turn in c['agent_data']['turns']:
                save(run / suite / 'sessions' / c['eval_case_id'] / f'turn_{turn["turn_index"]}.json', {
                    'fixture_provenance': 'synthetic', 'session_id': f'{suite}-{index}',
                    'events': [{'id': f'{suite}-{index}-{turn["turn_index"]}', 'invocation_id': 'fixture',
                                'usage_metadata': {'total_token_count': 8}}]})
        result = {'evaluation_dataset': [trace], 'eval_case_results': rows,
                  'summary_metrics': [{'metric_name': name, 'num_cases_total': count,
                                       'num_cases_valid': count, 'num_cases_error': 0} for name in payloads]}
        save(run / suite / 'traces.json', trace)
        save(run / suite / 'results.json', result)
        manifest['suites'][suite] = {'trace': f'{suite}/traces.json', 'results': f'{suite}/results.json',
                                    'trace_sha256': digest(run / suite / 'traces.json'),
                                    'results_sha256': digest(run / suite / 'results.json')}
    save(run / 'policy.json', policy)
    manifest['policy_sha256'] = digest(run / 'policy.json')
    save(run / 'manifest.json', manifest)
    return run
