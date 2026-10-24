"""Synthetic native-shaped fixtures verify ledger integrity; these are not Google grades."""
import copy
import json

import pytest

from adk_eval_harness.attempts import resolve
from adk_eval_harness.gate import digest

METRIC = 'final_response_match'


class Ledger:
    def __init__(self, root, metrics=None):
        self.root = root
        self.metrics = metrics or [METRIC]
        self.cases = [{'eval_case_id':cid, 'prompt':{'parts':[{'text':cid}]},
                       'responses':[{'response':{'parts':[{'text':'original answer'}]}}]}
                      for cid in ('a', 'b', 'c')]
        self.entry = {'trace':'original.json', 'attempts':[]}
        self.write('original.json', {'eval_cases':self.cases})
        self.entry['trace_sha256'] = digest(root/'original.json')
        self.requirements = {'cases':{c['eval_case_id']:{} for c in self.cases}, 'metrics':self.metrics}

    def write(self, path, data):
        target = self.root/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data))

    def attempt(self, scores, *, requested=None, native=None, code=0, result=True, missing=(), status=None):
        requested = requested or self.metrics[0]
        native = native or requested + ('_v2' if '_v' not in requested else '')
        number = len(self.entry['attempts'])
        folder = f'attempt-{number}'
        by_id = {c['eval_case_id']:c for c in self.cases}
        cases = [by_id[cid] for cid in scores]
        entry = {'trace':folder+'/traces.json', 'metric':requested,
                 'metric_config':folder+'/metrics.yaml', 'command':{'exit_code':code}}
        if status:
            entry['command']['status'] = status
        self.write(entry['trace'], {'eval_cases':cases})
        (self.root/entry['metric_config']).write_text('metrics_to_run:\n  - '+requested+'\n')
        for field in ('trace', 'metric_config'):
            entry[field+'_sha256'] = digest(self.root/entry[field])
        if result:
            rows, valid = [], 0
            for index, (cid, value) in enumerate(scores.items()):
                if cid in missing:
                    continue
                payload = copy.deepcopy(value) if isinstance(value, dict) else {'score':value}
                payload.setdefault('metric_name', native)
                if payload.get('score') is not None and not payload.get('error_message'):
                    valid += 1
                rows.append({'eval_case_index':index, 'response_candidate_results':[
                    {'response_index':0, 'metric_results':{native:payload}}]})
            native_result = {'evaluation_dataset':[{'eval_cases':cases}], 'eval_case_results':rows,
                             'summary_metrics':[{'metric_name':native, 'num_cases_total':len(cases),
                                                 'num_cases_valid':valid, 'num_cases_error':len(cases)-valid}]}
            entry['results'] = folder+'/results_001.json'
            self.write(entry['results'], native_result)
            entry['results_sha256'] = digest(self.root/entry['results'])
        self.entry['attempts'].append(entry)
        return entry

    def mutate_result(self, attempt, change):
        path = self.root/attempt['results']
        data = json.loads(path.read_text())
        change(data)
        self.write(attempt['results'], data)
        attempt['results_sha256'] = digest(path)

    def resolve(self):
        return resolve(self.root, 'synthetic', self.entry, self.requirements)


def test_recovery_keeps_first_valid_zero_and_native_payload_unchanged(tmp_path):
    ledger = Ledger(tmp_path)
    original = {'metric_name':METRIC+'_v2', 'score':0.0, 'explanation':'Valid low score',
                'raw_output':{'native':'unmodified'}, 'error_message':None}
    ledger.attempt({'a':original, 'b':None, 'c':{'score':None, 'error_message':'429 unavailable'}})
    ledger.attempt({'b':0.9, 'c':0.6})
    before = {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    effective, history, pending = ledger.resolve()
    assert pending == []
    selected = effective['eval_case_results'][0]['response_candidate_results'][0]['metric_results']
    assert selected[METRIC+'_v2'] == original
    assert history[1]['recovered_pairs'] == [['b',METRIC], ['c',METRIC]]
    assert all(e['recovered'] for e in history[0]['errors'])
    assert effective['metadata']['summary_origin'].startswith('derived counts')
    assert before == {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('score', [0.0, 1.0])
def test_completed_pair_cannot_be_retried_even_to_improve_score(tmp_path, score):
    ledger = Ledger(tmp_path)
    ledger.attempt({'a':0.0})
    ledger.attempt({'a':score})
    with pytest.raises(ValueError, match='retry of completed pair'):
        ledger.resolve()


def test_missing_rows_and_null_scores_remain_pending(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.attempt({'a':1, 'b':1, 'c':None}, missing=('b',))
    effective, _, pending = ledger.resolve()
    assert pending == [('b',METRIC), ('c',METRIC)]
    assert effective['summary_metrics'][0]['num_cases_valid'] == 1
    assert effective['summary_metrics'][0]['num_cases_error'] == 2


@pytest.mark.parametrize('mutate,message', [
    (lambda r:r['eval_case_results'].append(copy.deepcopy(r['eval_case_results'][0])), 'duplicate case index'),
    (lambda r:r['eval_case_results'][0].update(eval_case_index=True), 'invalid or duplicate case index'),
    (lambda r:r['summary_metrics'][0].update(num_cases_valid=0), 'summary counts disagree'),
    (lambda r:r['evaluation_dataset'][0]['eval_cases'][0]['prompt']['parts'][0].update(text='different'), 'dataset differs'),
    (lambda r:r['eval_case_results'][0]['response_candidate_results'][0].update(response_index=2), 'invalid native response index'),
    (lambda r:r['summary_metrics'].append(copy.deepcopy(r['summary_metrics'][0])), 'summary coverage')])
def test_corrupt_existing_results_block_recovery(tmp_path, mutate, message):
    ledger = Ledger(tmp_path)
    attempt = ledger.attempt({'a':1})
    ledger.mutate_result(attempt, mutate)
    with pytest.raises(ValueError, match=message):
        ledger.resolve()


@pytest.mark.parametrize('payload,message', [
    ({'score':'0.8'}, 'malformed native score'),
    ({'score':True}, 'malformed native score'),
    ({'score':1.2}, 'malformed native score'),
    ({'score':None, 'error_message':{'error':'429'}}, 'malformed native error_message'),
    ({'score':1, 'new_score':0}, 'unknown native metric payload'),
    ({'score':1, 'metric_name':'unknown_metric'}, 'malformed native metric payload')])
def test_malformed_payload_is_not_retryable(tmp_path, payload, message):
    ledger = Ledger(tmp_path)
    ledger.attempt({'a':payload})
    with pytest.raises(ValueError, match=message):
        ledger.resolve()


def test_native_version_cannot_drift_during_recovery(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.attempt({'a':None}, native=METRIC+'_v2')
    ledger.attempt({'a':1}, native=METRIC+'_v3')
    with pytest.raises(ValueError, match='version changed across attempts'):
        ledger.resolve()


@pytest.mark.parametrize('version,passes', [('v2',True), ('v3',False)])
def test_explicit_metric_version_is_exact(tmp_path, version, passes):
    ledger = Ledger(tmp_path, [METRIC+'_v2'])
    ledger.attempt({'a':1}, native=METRIC+'_'+version)
    if passes:
        assert len(ledger.resolve()[2]) == 2
    else:
        with pytest.raises(ValueError, match='does not match requested'):
            ledger.resolve()


def test_nonzero_command_with_valid_result_is_ambiguous(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.attempt({'a':0}, code=124)
    with pytest.raises(ValueError, match='ambiguous execution'):
        ledger.resolve()


@pytest.mark.parametrize('code,status', [(1,None), (None,'interrupted')])
def test_failed_or_confirmed_interrupted_without_result_can_recover(tmp_path, code, status):
    ledger = Ledger(tmp_path)
    ledger.attempt({'a':None}, code=code, status=status, result=False)
    ledger.attempt({'a':0.4})
    _, history, pending = ledger.resolve()
    assert ('a',METRIC) not in pending
    assert history[0]['errors'][0]['recovered']


def test_interrupted_with_unrecorded_result_is_not_retryable(tmp_path):
    ledger = Ledger(tmp_path)
    attempt = ledger.attempt({'a':None}, code=None, status='interrupted', result=False)
    attempt['grades_dir'] = 'attempt-0'
    ledger.write('attempt-0/results_unrecorded.json', {'cannot':'silently discard'})
    with pytest.raises(ValueError, match='undeclared or ambiguous'):
        ledger.resolve()


def test_successful_command_without_results_is_corrupt(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.attempt({'a':None}, result=False)
    with pytest.raises(ValueError, match='successful command without result'):
        ledger.resolve()


def test_explicit_missing_result_status_allows_recovery_after_exit_zero(tmp_path):
    ledger = Ledger(tmp_path)
    attempt = ledger.attempt({'a':None}, result=False)
    attempt['status'] = 'missing_result'
    ledger.attempt({'a':0.5})
    assert ('a',METRIC) not in ledger.resolve()[2]


@pytest.mark.parametrize('field', ['trace', 'metric_config', 'results'])
def test_artifact_hashes_are_enforced(tmp_path, field):
    ledger = Ledger(tmp_path)
    attempt = ledger.attempt({'a':1})
    (tmp_path/attempt[field]).write_text('altered')
    with pytest.raises(ValueError, match='hash mismatch'):
        ledger.resolve()


def test_hash_valid_modified_input_is_rejected(tmp_path):
    ledger = Ledger(tmp_path)
    attempt = ledger.attempt({'a':None})
    changed = copy.deepcopy(ledger.cases[0])
    changed['prompt']['parts'][0]['text'] = 'altered'
    ledger.write(attempt['trace'], {'eval_cases':[changed]})
    attempt['trace_sha256'] = digest(tmp_path/attempt['trace'])
    with pytest.raises(ValueError, match='differs from original trace'):
        ledger.resolve()


def test_attempt_input_must_preserve_state_only_events_even_if_native_export_drops_them(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.cases[0]['agent_data'] = {'turns':[{'events':[{'author':'agent', 'state_delta':{'count':1}}]}]}
    ledger.write('original.json', {'eval_cases':ledger.cases})
    ledger.entry['trace_sha256'] = digest(tmp_path/'original.json')
    attempt = ledger.attempt({'a':None})
    changed = copy.deepcopy(ledger.cases[0])
    changed['agent_data']['turns'][0]['events'].clear()
    ledger.write(attempt['trace'], {'eval_cases':[changed]})
    attempt['trace_sha256'] = digest(tmp_path/attempt['trace'])
    with pytest.raises(ValueError, match='differs from original trace'):
        ledger.resolve()


def test_artifact_paths_cannot_escape_run(tmp_path):
    ledger = Ledger(tmp_path)
    attempt = ledger.attempt({'a':1})
    attempt['results'] = '../escaped.json'
    with pytest.raises(ValueError, match='path escapes run'):
        ledger.resolve()


def test_effective_view_retains_all_metrics_and_original_order(tmp_path):
    ledger = Ledger(tmp_path, [METRIC, 'safety'])
    ledger.attempt({'c':0.6, 'a':0.4}, requested=METRIC)
    ledger.attempt({'a':1, 'b':1, 'c':1}, requested='safety')
    effective, _, pending = ledger.resolve()
    assert pending == [('b',METRIC)]
    assert [c['eval_case_id'] for c in effective['evaluation_dataset'][0]['eval_cases']] == ['a','b','c']
    assert len(effective['summary_metrics']) == 2
    assert all(len(r['response_candidate_results'][0]['metric_results']) == 2 for r in effective['eval_case_results'])


def test_metric_config_cannot_swap_grader_despite_valid_hash(tmp_path):
    ledger = Ledger(tmp_path)
    attempt = ledger.attempt({'a':1})
    path = tmp_path/attempt['metric_config']
    path.write_text('metrics_to_run:\n  - safety\n')
    attempt['metric_config_sha256'] = digest(path)
    with pytest.raises(ValueError, match='metric config must select only'):
        ledger.resolve()


def test_declared_result_cannot_disappear_and_be_reclassified_unavailable(tmp_path):
    ledger = Ledger(tmp_path)
    attempt = ledger.attempt({'a':1})
    (tmp_path/attempt['results']).unlink()
    with pytest.raises(ValueError, match='missing results artifact'):
        ledger.resolve()


def test_unknown_exit_status_with_existing_results_blocks(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.attempt({'a':None}, code=None, status='interrupted')
    with pytest.raises(ValueError, match='finished exit code'):
        ledger.resolve()


@pytest.mark.parametrize('missing_candidate', [False, True])
def test_missing_metric_or_candidate_with_consistent_summary_is_pending(tmp_path, missing_candidate):
    ledger = Ledger(tmp_path)
    attempt = ledger.attempt({'a':None})
    def change(result):
        candidate = result['eval_case_results'][0]['response_candidate_results']
        if missing_candidate:
            candidate.clear()
        else:
            candidate[0]['metric_results'].clear()
    ledger.mutate_result(attempt, change)
    assert ('a',METRIC) in ledger.resolve()[2]


@pytest.mark.parametrize('location', ['args', 'tool_result', 'state_delta', 'part_metadata', 'tool_schema'])
def test_native_dataset_cannot_omit_null_payload_keys(tmp_path, location):
    ledger = Ledger(tmp_path)
    event = {'author': 'fixture', 'content': {'parts': [{'text': 'answer'}]}}
    part = event['content']['parts'][0]
    if location == 'args':
        part['function_call'] = {'name': 'clear', 'args': {'nested': {'value': None}}}
        payload = part['function_call']['args']['nested']
    elif location == 'tool_result':
        part['function_response'] = {'name': 'clear', 'response': {'nested': {'value': None}}}
        payload = part['function_response']['response']['nested']
    elif location == 'state_delta':
        event['state_delta'] = {'nested': {'value': None}}
        payload = event['state_delta']['nested']
    elif location == 'part_metadata':
        part['part_metadata'] = {'value': None}
        payload = part['part_metadata']
    else:
        event['active_tools'] = [{'function_declarations': [{'name': 'clear', 'parameters_json_schema':
                                {'type': 'object', 'default': None}}]}]
        payload = event['active_tools'][0]['function_declarations'][0]['parameters_json_schema']
    ledger.cases[0]['agent_data'] = {'turns': [{'events': [event]}]}
    ledger.write('original.json', {'eval_cases': ledger.cases})
    ledger.entry['trace_sha256'] = digest(tmp_path / 'original.json')
    attempt = ledger.attempt({'a': 1})
    assert len(ledger.resolve()[2]) == 2  # Preserved null payload is valid.
    # Change only the returned grading dataset, leaving its recorded trace unchanged.
    key = 'default' if location == 'tool_schema' else 'value'
    payload.pop(key)
    ledger.mutate_result(attempt, lambda result: result['evaluation_dataset'][0]['eval_cases'][0].update(
        agent_data=ledger.cases[0]['agent_data']))
    with pytest.raises(ValueError, match='dataset differs'):
        ledger.resolve()


def test_sdk_optional_null_fields_remain_comparable(tmp_path):
    ledger = Ledger(tmp_path)
    attempt = ledger.attempt({'a': 1})
    def optional_nulls(result):
        case = result['evaluation_dataset'][0]['eval_cases'][0]
        case.update(system_instruction=None, agent_data=None)
        case['prompt']['role'] = None
        case['prompt']['parts'][0].update(thought=None, function_call=None)
    ledger.mutate_result(attempt, optional_nulls)
    assert len(ledger.resolve()[2]) == 2
