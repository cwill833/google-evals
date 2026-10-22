"""Synthetic native-shaped artifacts test schema-3 recovery policy, not judge quality."""
import json

import pytest

from adk_eval_harness.gate import digest, evaluate
from adk_eval_harness.runner import VERSIONS

METRIC = 'final_response_match'


def setup_run(root, *, advisory=False):
    content = {'role':'model', 'parts':[{'text':'answer'}]}
    case = {'eval_case_id':'case', 'prompt':{'parts':[{'text':'question'}]},
            'agent_data':{'turns':[{'turn_index':0, 'events':[{'author':'agent', 'content':content}]}]},
            'responses':[{'response':content}]}
    policy = {'schema_version':2, 'thresholds':{METRIC:0.8},
              'suites':{'suite':{'metrics':[METRIC], 'cases':{'case':{}}}}}
    if advisory:
        policy.update(metric_roles={METRIC:'advisory'}, calibration_rationale='Synthetic test policy')
    write(root/'policy.json', policy)
    write(root/'original.json', {'eval_cases':[case]})
    manifest = {'schema_version':3, 'generation_complete':True, 'versions':VERSIONS,
                'policy_sha256':digest(root/'policy.json'), 'errors':[],
                'commands':[{'exit_code':0, 'argv':['python','-m','adk_eval_harness','generate']}],
                'suites':{'suite':{'trace':'original.json', 'trace_sha256':digest(root/'original.json'), 'attempts':[]}}}
    return manifest, case


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def add_attempt(root, manifest, case, score, error=None, *, code=0, native=METRIC+'_v2'):
    attempts = manifest['suites']['suite']['attempts']
    folder = root/f'attempt-{len(attempts)}'
    write(folder/'trace.json', {'eval_cases':[case]})
    (folder/'metrics.yaml').write_text('metrics_to_run:\n  - '+METRIC+'\n')
    valid = score is not None and not error
    payload = {'metric_name':native, 'score':score, 'error_message':error, 'explanation':'Synthetic fixture'}
    write(folder/'results.json', {'evaluation_dataset':[{'eval_cases':[case]}],
        'eval_case_results':[{'eval_case_index':0, 'response_candidate_results':[{'response_index':0,
            'metric_results':{native:payload}}]}],
        'summary_metrics':[{'metric_name':native, 'num_cases_total':1,
                            'num_cases_valid':int(valid), 'num_cases_error':int(not valid)}]})
    attempt = {'metric':METRIC, 'command':{'exit_code':code}}
    for field, filename in [('trace','trace.json'), ('metric_config','metrics.yaml'), ('results','results.json')]:
        attempt[field] = str((folder/filename).relative_to(root))
        attempt[field+'_sha256'] = digest(folder/filename)
    attempts.append(attempt)


def check(root, manifest):
    write(root/'manifest.json', manifest)
    return evaluate(root, root/'policy.json')


def test_recovered_grades_pass_with_original_failure_visible(tmp_path):
    manifest, case = setup_run(tmp_path)
    add_attempt(tmp_path, manifest, case, None, '429 temporarily unavailable')
    add_attempt(tmp_path, manifest, case, 1.0)
    report = check(tmp_path, manifest)
    assert report['passed'] and report['evaluation_complete']
    assert report['recovered_grading_failures'] == 1
    assert not report['pending_grades']
    assert any('429 temporarily unavailable' in warning for warning in report['warnings'])
    assert report['grading_attempts']['suite'][0]['errors'][0]['recovered']
    assert report['scores'][0]['native_metric_name'] == METRIC+'_v2'


def test_recovery_does_not_turn_a_valid_low_score_into_success(tmp_path):
    manifest, case = setup_run(tmp_path)
    add_attempt(tmp_path, manifest, case, None, '400 invalid judge output')
    add_attempt(tmp_path, manifest, case, 0.0)
    report = check(tmp_path, manifest)
    assert not report['passed'] and report['evaluation_complete']
    assert report['scores'][0]['score'] == 0.0
    assert report['recovered_grading_failures'] == 1
    add_attempt(tmp_path, manifest, case, 1.0)
    report = check(tmp_path, manifest)
    assert not report['passed'] and not report['evaluation_complete']
    assert any('retry of completed pair' in error for error in report['errors'])


def test_advisory_metric_unavailable_still_blocks_release(tmp_path):
    manifest, case = setup_run(tmp_path, advisory=True)
    add_attempt(tmp_path, manifest, case, None, '429 quota exhausted')
    report = check(tmp_path, manifest)
    assert not report['passed'] and not report['evaluation_complete']
    assert report['pending_grades'] == [{'suite':'suite', 'case':'case', 'metric':METRIC}]
    assert report['grading_attempts']['suite'][0]['errors'][0]['recovered'] is False


@pytest.mark.parametrize('generation_complete', [False, None, 1])
def test_grade_recovery_cannot_hide_incomplete_generation(tmp_path, generation_complete):
    manifest, case = setup_run(tmp_path)
    manifest['generation_complete'] = generation_complete
    add_attempt(tmp_path, manifest, case, 1.0)
    report = check(tmp_path, manifest)
    assert not report['passed'] and not report['evaluation_complete']
    assert any('generation is incomplete' in error for error in report['errors'])


def test_nonzero_generation_command_is_not_a_recoverable_grading_error(tmp_path):
    manifest, case = setup_run(tmp_path)
    manifest['commands'][0]['exit_code'] = 1
    add_attempt(tmp_path, manifest, case, 1.0)
    report = check(tmp_path, manifest)
    assert not report['passed'] and not report['evaluation_complete']


def test_grade_commands_cannot_bypass_ledger_in_schema3(tmp_path):
    manifest, case = setup_run(tmp_path)
    manifest['commands'].append({'exit_code':0, 'argv':['agents-cli','eval','grade']})
    add_attempt(tmp_path, manifest, case, 1.0)
    report = check(tmp_path, manifest)
    assert not report['passed']
    assert any('must be recorded in the attempt ledger' in error for error in report['errors'])


def test_old_schema_keeps_single_result_behavior(tmp_path):
    manifest, case = setup_run(tmp_path)
    add_attempt(tmp_path, manifest, case, 1.0)
    attempt = manifest['suites']['suite'].pop('attempts')[0]
    manifest['schema_version'] = 2
    manifest.pop('generation_complete')
    manifest['suites']['suite'].update({k:attempt[k] for k in ['results', 'results_sha256']})
    report = check(tmp_path, manifest)
    assert report['passed']
    assert not report['grading_attempts'] and report['recovered_grading_failures'] == 0
