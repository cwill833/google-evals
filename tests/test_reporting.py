"""Reports expose evidence and telemetry without inventing native scores or usage."""
import copy
import json

from adk_eval_harness.gate import digest, read
from adk_eval_harness.reporting import usage_summary, write_report
from adk_eval_harness.runner import VERSIONS
from tests.fixture_runs import make_run

METRIC = 'final_response_match_v2'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path, *, telemetry=True):
    run = tmp_path / 'run'
    user = {'role': 'user', 'parts': [{'text': '<script>alert("user")</script> & inspect order'}]}
    reply = {'role': 'model', 'parts': [{'text': '{"status": "open"}'}]}
    trace = {'eval_cases': [{'eval_case_id': 'case-1', 'prompt': user, 'responses': [{'response': reply}],
             'agent_data': {'turns': [{'turn_index': 0, 'events': [
                 {'author': 'user', 'content': user},
                 {'author': 'fixture', 'content': {'parts': [{'function_call': {'id': 'tool-1', 'name': 'inspect', 'args': {'id': 'A'}}}]}},
                 {'author': 'fixture', 'content': {'parts': [{'function_response': {'id': 'tool-1', 'name': 'inspect', 'response': {'status': 'open'}}}]}},
                 {'author': 'fixture', 'content': reply}]}]}}]}
    grade = {'metric_name': METRIC, 'score': 1, 'explanation': '<img src=x onerror=alert(1)>Matches reference.',
             'rubric_verdicts': [{'rubric': {'description': 'Exact output'}, 'verdict': True}]}
    result = {'evaluation_dataset': [trace], 'eval_case_results': [{'eval_case_index': 0,
              'response_candidate_results': [{'response_index': 0, 'metric_results': {METRIC: grade}}]}],
              'summary_metrics': [{'metric_name': METRIC, 'num_cases_total': 1, 'num_cases_valid': 1, 'num_cases_error': 0}]}
    save(run / 'suite/traces.json', trace)
    save(run / 'suite/grades/results.json', result)
    save(run / 'policy.json', {'schema_version': 2, 'thresholds': {METRIC: 1},
          'suites': {'suite': {'metrics': [METRIC], 'cases': {'case-1': {}}}}})
    save(run / 'manifest.json', {'schema_version': 2, 'agent': 'fixture:root_agent',
        'policy_sha256': digest(run / 'policy.json'), 'versions': VERSIONS,
        'commands': [{'exit_code': 0}], 'errors': [], 'suites': {'suite': {
        'trace': 'suite/traces.json', 'trace_sha256': digest(run / 'suite/traces.json'),
        'results': 'suite/grades/results.json', 'results_sha256': digest(run / 'suite/grades/results.json')}}})
    save(run / 'gate.json', {'passed': True, 'evaluation_complete': True, 'errors': [], 'warnings': [], 'scores': []})
    raw = {'session_id': 'session-1', 'user_message': user, 'events': [
        {'id': 'e1', 'invocation_id': 'i1', 'usage_metadata': {'prompt_token_count': 10, 'candidates_token_count': 3, 'total_token_count': 13}},
        {'id': 'e2', 'invocation_id': 'i1', 'usage_metadata': {'prompt_token_count': 20, 'candidates_token_count': 4, 'total_token_count': 24}}]}
    if telemetry:
        raw['telemetry'] = {'duration_seconds': 1.25, 'time_to_first_event_seconds': .2,
                            'time_to_first_text_seconds': .4, 'clock': 'client_monotonic',
                            'scope': 'turn_execution_and_state_snapshot', 'status': 'success'}
    save(run / 'suite/sessions/case-1/turn_0.json', raw)
    return run


def test_report_renders_actual_native_payload_tools_and_measured_telemetry(tmp_path):
    run = fixture(tmp_path)
    before = (run / 'suite/grades/results.json').read_bytes()
    report = write_report(run)
    assert report['passed'] and report['case_count'] == 1
    assert report['native_grade_count'] == 1
    assert report['agent_usage']['observed_token_counts']['total_token_count'] == 37
    assert report['agent_usage']['observed_token_counts']['thoughts_token_count'] is None
    assert report['agent_latency']['p95_turn_seconds'] == 1.25
    assert report['judge_usage']['token_counts'] is None
    case = report['cases'][0]
    assert case['native_grades'][0]['payload']['rubric_verdicts'][0]['verdict'] is True
    assert case['turns'][0]['events'][1]['tool_calls'][0]['name'] == 'inspect'
    assert case['turns'][0]['events'][2]['tool_responses'][0]['response'] == {'status': 'open'}
    assert (run / 'suite/grades/results.json').read_bytes() == before
    markup = (run / 'report.html').read_text()
    assert '<script>' not in markup and '<img src=x' not in markup
    assert '&lt;script&gt;' in markup and '&lt;img src=x' in markup
    assert 'href="suite/traces.json"' in markup
    assert 'Unavailable' in markup
    assert read(run / 'report.json')['cases'] == report['cases']


def test_legacy_missing_telemetry_is_unavailable_not_timestamp_estimate(tmp_path):
    run = fixture(tmp_path, telemetry=False)
    raw_path = run / 'suite/sessions/case-1/turn_0.json'
    raw = read(raw_path)
    raw['events'] = [{'timestamp': 100}, {'timestamp': 200}]
    save(raw_path, raw)
    report = write_report(run)
    assert report['agent_latency']['observed_duration_seconds'] is None
    assert report['agent_latency']['recorded_turns'] == 0
    assert report['agent_usage']['status'] == 'unavailable'
    assert all(value is None for value in report['agent_usage']['observed_token_counts'].values())


def test_stream_usage_is_deduplicated_without_adding_partial_chunks():
    final = {'id': 'final', 'invocation_id': 'inv', 'usage_metadata': {'total_token_count': 100}}
    events = [{'id': 'chunk1', 'invocation_id': 'inv', 'partial': True,
               'usage_metadata': {'total_token_count': 50}}, final, copy.deepcopy(final),
              {'id': 'second-call', 'invocation_id': 'inv', 'usage_metadata': {'total_token_count': 30}}]
    report = usage_summary(events)
    assert report['observed_token_counts']['total_token_count'] == 130
    assert report['excluded_partial_events'] == 1
    assert report['duplicate_events_excluded'] == 1
    assert report['measured_events'] == 2
    assert report['status'] == 'partial_or_uncertain'


def test_escaped_or_modified_artifacts_never_render_a_pass(tmp_path):
    run = fixture(tmp_path)
    manifest = read(run / 'manifest.json')
    (tmp_path / 'outside.json').write_text('{"secret": "must not read"}')
    manifest['suites']['suite']['trace'] = '../outside.json'
    manifest['suites']['suite']['trace_sha256'] = digest(tmp_path / 'outside.json')
    save(run / 'manifest.json', manifest)
    report = write_report(run)
    assert not report['passed'] and not report['evaluation_complete']
    assert 'must not read' not in (run / 'report.html').read_text()
    assert 'href="../' not in (run / 'report.html').read_text()


def test_recovery_report_retains_failed_attempt_and_native_explanations(tmp_path):
    run = fixture(tmp_path)
    manifest = read(run / 'manifest.json')
    manifest['schema_version'] = 3
    manifest['generation_complete'] = True
    entry = manifest['suites']['suite']
    native = read(run / 'suite/grades/results.json')
    entry.pop('results'); entry.pop('results_sha256')
    entry['attempts'] = []
    for index, failed in enumerate((True, False)):
        folder = run / 'suite/attempts' / str(index)
        result = copy.deepcopy(native)
        if failed:
            payload = result['eval_case_results'][0]['response_candidate_results'][0]['metric_results'][METRIC]
            payload.update(score=None, error_message='SYNTHETIC judge unavailable')
            result['summary_metrics'][0].update(num_cases_valid=0, num_cases_error=1)
        save(folder / 'traces.json', read(run / 'suite/traces.json'))
        save(folder / 'results.json', result)
        (folder / 'metrics.yaml').write_text(f'metrics_to_run: [{METRIC}]\n')
        attempt = {'metric': METRIC, 'command': {'exit_code': 0}}
        for key, filename in [('trace', 'traces.json'), ('results', 'results.json'), ('metric_config', 'metrics.yaml')]:
            attempt[key] = str((folder / filename).relative_to(run))
            attempt[key + '_sha256'] = digest(folder / filename)
        entry['attempts'].append(attempt)
    save(run / 'manifest.json', manifest)
    gate = read(run / 'gate.json'); gate['recovered_grading_failures'] = 1
    save(run / 'gate.json', gate)
    report = write_report(run)
    assert report['passed']
    attempts = report['cases'][0]['attempts']
    assert len(attempts) == 2
    assert attempts[0]['errors'][0]['recovered'] is True
    assert report['cases'][0]['native_grades'][0]['payload']['score'] == 1
    assert 'SYNTHETIC judge unavailable' in (run / 'report.html').read_text()


def test_legacy_multisuite_run_gets_a_useful_report(tmp_path):
    run = make_run(tmp_path)
    report = write_report(run)
    assert report['passed']
    assert report['case_count'] == 4
    assert report['native_grade_count'] == 4
    assert report['agent_latency']['status'] == 'unavailable'
    assert report['agent_usage']['observed_token_counts']['total_token_count'] == 64


def test_stale_forged_gate_cannot_override_native_failures(tmp_path):
    run = fixture(tmp_path)
    result_path = run / 'suite/grades/results.json'
    result = read(result_path)
    result['eval_case_results'][0]['response_candidate_results'][0]['metric_results'][METRIC]['score'] = 0
    save(result_path, result)
    manifest = read(run / 'manifest.json')
    manifest['suites']['suite']['results_sha256'] = digest(result_path)
    manifest['execution'] = {'mode': 'remote', 'endpoint': 'https://test.invalid', 'expected_manifest_sha256': 'abc'}
    save(run / 'manifest.json', manifest)
    report = write_report(run)
    assert report['recorded_gate']['passed'] is True
    assert report['passed'] is False and report['evaluation_complete'] is True
    assert report['cases'][0]['verdict'] == 'failed'
    assert report['execution']['mode'] == 'remote'
    assert 'https://test.invalid' in (run / 'report.html').read_text()
    assert any(link['path'] == 'gate.json' and link['sha256'] == digest(run / 'gate.json') for link in report['artifacts'])


def test_report_replays_policy_hash_and_recovery_lineage(tmp_path):
    run = fixture(tmp_path)
    policy_path = run / 'policy.json'
    policy_path.write_text(policy_path.read_text() + '\n')
    report = write_report(run)
    assert not report['passed'] and not report['evaluation_complete']
    assert any('policy hash mismatch' in error for error in report['errors'])
    manifest = read(run / 'manifest.json')
    manifest.update(schema_version=3, generation_complete=True, parent={'manifest': '../outside.json', 'manifest_sha256': 'x'})
    save(run / 'manifest.json', manifest)
    report = write_report(run)
    assert not report['passed'] and not report['evaluation_complete']
    assert any('escapes run' in error for error in report['errors'])


def test_generation_failure_surfaces_only_known_partial_cases(tmp_path):
    run = fixture(tmp_path)
    manifest = read(run / 'manifest.json')
    manifest.update(schema_version=3, generation_complete=False, suites={}, errors=['Synthetic generation timeout'])
    save(run / 'manifest.json', manifest)
    raw_path = run / 'suite/sessions/case-1/turn_0.json'
    raw = read(raw_path)
    raw['events'] = [{'author': 'fixture', 'content': {'parts': [{'text': 'Partial reply'},
                         {'function_call': {'id': 'f1', 'name': 'inspect', 'args': {'id': 'A'}}}]}}]
    raw['telemetry'].update(status='error', error_class='TimeoutError')
    save(raw_path, raw)
    save(run / 'suite/sessions/unknown-case/turn_0.json', {'user_message': {'parts': [{'text': 'SHOULD_NOT_READ'}]}, 'events': []})
    report = write_report(run)
    assert not report['passed'] and not report['evaluation_complete']
    assert report['case_count'] == report['partial_case_count'] == 1
    assert report['native_grade_count'] == 0
    case = report['cases'][0]
    assert case['verdict'] == 'incomplete' and case['evidence_status'] == 'partial_ungraded'
    assert case['native_grades'] == [] and case['final_response'] == ''
    assert case['turns'][0]['events'][0]['author'] == 'user'
    assert case['turns'][0]['events'][1]['text'] == 'Partial reply'
    assert case['turns'][0]['events'][1]['tool_calls'][0]['name'] == 'inspect'
    assert case['turns'][0]['telemetry']['error_class'] == 'TimeoutError'
    markup = (run / 'report.html').read_text()
    assert 'SHOULD_NOT_READ' not in markup
    assert 'ungraded and incomplete' in markup


def test_bound_telemetry_is_verified_and_state_metadata_links_are_included(tmp_path):
    run = fixture(tmp_path)
    save(run / 'suite/sessions/case-1/state_0.json', {'state': {'count': 1}})
    save(run / 'suite/sessions/case-1/runner_metadata.json', {'mode': 'remote', 'server_identity': 'fixture'})
    manifest = read(run / 'manifest.json')
    manifest['telemetry_sha256'] = {str(path.relative_to(run)): digest(path) for path in (run / 'suite/sessions').rglob('*.json')}
    manifest['commands'] = [{'exit_code': 0, 'elapsed_seconds': 3.0, 'argv': ['python', 'generate']},
                            {'exit_code': 0, 'elapsed_seconds': 8.0, 'argv': ['agents-cli', 'eval', 'grade']}]
    save(run / 'manifest.json', manifest)
    report = write_report(run)
    assert report['passed']
    assert report['telemetry_integrity']['binding'] == 'manifest_sha256'
    assert report['agent_usage']['observed_token_counts']['total_token_count'] == 37
    links = [artifact['path'] for artifact in report['cases'][0]['artifacts']]
    assert 'suite/sessions/case-1/state_0.json' in links
    assert 'suite/sessions/case-1/runner_metadata.json' in links
    assert report['runtime_summary']['generation_and_preflight_commands']['observed_seconds'] == 3
    assert report['runtime_summary']['native_grading_commands']['observed_seconds'] == 8


def test_changed_bound_telemetry_is_excluded_and_report_fails_closed(tmp_path):
    run = fixture(tmp_path)
    raw_path = run / 'suite/sessions/case-1/turn_0.json'
    manifest = read(run / 'manifest.json')
    manifest['telemetry_sha256'] = {str(raw_path.relative_to(run)): digest(raw_path)}
    save(run / 'manifest.json', manifest)
    raw = read(raw_path)
    raw['events'][0]['usage_metadata']['total_token_count'] = 999999
    save(raw_path, raw)
    report = write_report(run)
    assert not report['passed'] and not report['evaluation_complete']
    assert report['agent_usage']['observed_token_counts']['total_token_count'] is None
    assert report['agent_latency']['observed_duration_seconds'] is None
    assert report['telemetry_integrity']['errors']
    assert '999999' not in (run / 'report.html').read_text()


def test_legacy_telemetry_explicitly_reports_its_unbound_provenance(tmp_path):
    report = write_report(fixture(tmp_path))
    assert report['telemetry_integrity']['binding'] == 'unbound_historical'
    assert 'Unbound historical telemetry' in report['telemetry_integrity']['note']
    assert report['agent_usage']['observed_token_counts']['total_token_count'] == 37


def test_unavailable_grading_payload_is_visible_but_not_counted_as_completed(tmp_path):
    run=fixture(tmp_path)
    manifest=read(run/'manifest.json')
    manifest.update(schema_version=3,generation_complete=True)
    entry=manifest['suites']['suite']
    result_path=run/'suite/grades/results.json'
    result=read(result_path)
    payload=result['eval_case_results'][0]['response_candidate_results'][0]['metric_results'][METRIC]
    payload.update(score=None,error_message='SYNTHETIC quota unavailable')
    result['summary_metrics'][0].update(num_cases_valid=0,num_cases_error=1)
    save(result_path,result)
    metrics=run/'suite/metrics.yaml';metrics.write_text(f'metrics_to_run: [{METRIC}]\n')
    attempt={'metric':METRIC,'command':{'exit_code':0},'trace':entry['trace'],
             'trace_sha256':entry['trace_sha256'],'metric_config':'suite/metrics.yaml',
             'metric_config_sha256':digest(metrics),'results':entry.pop('results'),
             'results_sha256':digest(result_path)}
    entry.pop('results_sha256');entry['attempts']=[attempt]
    save(run/'manifest.json',manifest)
    report=write_report(run)
    assert report['native_grade_count']==0
    assert report['requested_grade_count']==report['unavailable_grade_count']==1
    assert report['cases'][0]['native_grades'][0]['payload']['score'] is None
    assert '0 / 1 completed native grades' in (run/'report.html').read_text()
    assert 'SYNTHETIC quota unavailable' in (run/'report.html').read_text()
    # A valid zero is completed evidence even though it fails the quality threshold.
    payload['score']=0;payload.pop('error_message')
    result['summary_metrics'][0].update(num_cases_valid=1,num_cases_error=0)
    save(result_path,result);attempt['results_sha256']=digest(result_path)
    save(run/'manifest.json',manifest)
    report=write_report(run)
    assert report['native_grade_count']==1 and report['unavailable_grade_count']==0
    assert not report['passed']
