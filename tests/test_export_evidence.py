"""Evidence exports retain native bytes and recursively bind sanitized metadata."""
import json
from pathlib import Path

import pytest

from adk_eval_harness.gate import digest, read
from adk_eval_harness.attempts import resolve
from adk_eval_harness.lineage import verify_lineage
from scripts import export_evidence


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def fixture_run(root):
    root.mkdir()
    case = {'eval_case_id':'case', 'responses':[{'response':{'parts':[{'text':'answer'}]}}]}
    save(root/'trace.json', {'eval_cases':[case]})
    save(root/'attempt/trace.json', {'eval_cases':[case]})
    (root/'attempt/metrics.yaml').write_text('metrics_to_run: [final_response_match_v2]\n')
    save(root/'attempt/results_1.json', {'evaluation_dataset':[{'eval_cases':[case]}],
        'eval_case_results':[{'eval_case_index':0,'response_candidate_results':[{'response_index':0,
            'metric_results':{'final_response_match_v2':{'metric_name':'final_response_match_v2','score':0.0}}}]}],
        'summary_metrics':[{'metric_name':'final_response_match_v2','num_cases_total':1,'num_cases_valid':1,'num_cases_error':0}]})
    attempt = {'trace':'attempt/trace.json', 'metric_config':'attempt/metrics.yaml',
               'results':'attempt/results_1.json', 'metric':'final_response_match_v2',
               'command':{'exit_code':0,'log':str(export_evidence.ROOT/'private.log')}}
    for key in ('trace','results','metric_config'):
        attempt[key+'_sha256'] = digest(root/attempt[key])
    manifest = {'schema_version':3, 'generation_complete':True, 'suites':{'suite':{'trace':'trace.json',
        'trace_sha256':digest(root/'trace.json'),'attempts':[attempt]}}}
    # Two generations of metadata snapshots establish bottom-up hash rebinding.
    save(root/'history/first.gate.json', {'passed':False,'log':str(export_evidence.ROOT/'private.log')})
    save(root/'history/first.manifest.json', manifest)
    manifest['parent'] = {'manifest':'history/first.manifest.json',
                         'manifest_sha256':digest(root/'history/first.manifest.json'),
                         'gate':'history/first.gate.json','gate_sha256':digest(root/'history/first.gate.json')}
    save(root/'history/second.manifest.json', manifest)
    manifest['parent'] = {'manifest':'history/second.manifest.json',
                         'manifest_sha256':digest(root/'history/second.manifest.json')}
    save(root/'manifest.json', manifest)
    return manifest


def test_schema3_export_preserves_native_bytes_and_rebinds_entire_lineage(tmp_path):
    source,destination = tmp_path/'source',tmp_path/'exported'
    original = fixture_run(source)
    export_evidence.export(source,destination)
    exported = read(destination/'manifest.json')
    verify_lineage(destination, exported)
    for name in ('trace.json','attempt/trace.json','attempt/metrics.yaml','attempt/results_1.json'):
        assert (source/name).read_bytes() == (destination/name).read_bytes()
    assert exported['suites']['suite']['attempts'][0]['results_sha256'] == original['suites']['suite']['attempts'][0]['results_sha256']
    current = exported
    while current.get('parent'):
        parent = current['parent']
        assert digest(destination/parent['manifest']) == parent['manifest_sha256']
        if parent.get('gate'):
            assert digest(destination/parent['gate']) == parent['gate_sha256']
        current = read(destination/parent['manifest'])
    assert str(export_evidence.ROOT) not in (destination/'manifest.json').read_text()
    assert exported['export']['raw_manifest_sha256'] == digest(source/'manifest.json')
    _, _, pending = resolve(destination,'suite',exported['suites']['suite'],
                            {'cases':{'case':{}},'metrics':['final_response_match_v2']})
    assert pending == []


@pytest.mark.parametrize('artifact', ['trace.json','attempt/results_1.json','attempt/metrics.yaml'])
def test_private_identifier_in_immutable_evidence_refuses_without_partial_export(tmp_path,monkeypatch,artifact):
    source,destination = tmp_path/'source',tmp_path/'exported'
    manifest = fixture_run(source)
    # Avoid hash mismatches obscuring the privacy refusal.
    token = 'private-project-for-test'
    path=source/artifact
    path.write_text(path.read_text()+'\n'+token)
    for manifest_path in [source/'manifest.json',*source.glob('history/*.manifest.json')]:
        data=read(manifest_path)
        for entry in [data['suites']['suite'],*data['suites']['suite']['attempts']]:
            for field in ('trace','results','metric_config'):
                if entry.get(field)==artifact:entry[field+'_sha256']=digest(path)
        data.pop('parent',None)
        save(manifest_path,data)
    monkeypatch.setattr(export_evidence,'dotenv_values',lambda _: {'GOOGLE_CLOUD_PROJECT':token})
    with pytest.raises(ValueError,match='Private identifier in immutable evidence'):
        export_evidence.export(source,destination)
    assert not destination.exists()


def test_corrupt_hash_cannot_be_laundered_by_export(tmp_path):
    source,destination=tmp_path/'source',tmp_path/'exported'
    fixture_run(source)
    with (source/'attempt/results_1.json').open('a') as stream:stream.write(' ')
    with pytest.raises(ValueError,match='hash-mismatched'):
        export_evidence.export(source,destination)
    assert not destination.exists()


def test_partial_native_shaped_results_are_recoverable_without_rewriting(tmp_path):
    from tests.test_attempts import Ledger
    source = tmp_path / 'source'; source.mkdir()
    ledger = Ledger(source)
    attempt = ledger.attempt({'a': 0.0, 'b': 1.0, 'c': {'score': None, 'error_message': 'SYNTHETIC unavailable'}})
    save(source / 'manifest.json', {'schema_version': 3, 'suites': {'load': ledger.entry}})
    original = (source / attempt['results']).read_bytes()
    destination = tmp_path / 'exported'
    export_evidence.export(source, destination)
    effective, _, pending = resolve(destination, 'load', ledger.entry, ledger.requirements)
    scores = [next(iter(row['response_candidate_results'][0]['metric_results'].values()))['score']
              for row in effective['eval_case_results']]
    assert pending == [('c', 'final_response_match')]
    assert scores == [0.0, 1.0, None]
    assert (destination / attempt['results']).read_bytes() == original


def test_derived_reports_are_regenerated_and_hosted_endpoint_is_removed(tmp_path, monkeypatch):
    from tests.test_reporting import fixture
    from adk_eval_harness.reporting import write_report
    source = fixture(tmp_path)
    endpoint = 'https://private-project-for-test-123.example.run.app'
    manifest = read(source/'manifest.json')
    manifest['execution'] = {'mode':'hosted', 'endpoint':endpoint, 'auth':'gcloud'}
    manifest['telemetry_sha256'] = {str(path.relative_to(source)):digest(path)
        for path in (source/'suite/sessions').rglob('*.json')}
    save(source/'manifest.json',manifest)
    write_report(source)
    # These are replaceable views, not the immutable native artifacts.
    (source/'report.html').write_text(str(export_evidence.ROOT) + ' STALE_REPORT ' + endpoint)
    (source/'report.json').write_text('not even valid JSON')
    monkeypatch.setattr(export_evidence,'dotenv_values',lambda _: {'GOOGLE_CLOUD_PROJECT':'private-project-for-test'})
    destination=tmp_path/'exported';export_evidence.export(source,destination)
    result=read(destination/'report.json')
    assert result['passed']
    assert result['execution']['endpoint']=='<hosted-endpoint>'
    assert result['telemetry_integrity']['binding']=='manifest_sha256'
    text=(destination/'report.html').read_text()
    assert endpoint not in text and 'STALE_REPORT' not in text and str(export_evidence.ROOT) not in text
    for path in [source/'suite/traces.json',source/'suite/grades/results.json',*list((source/'suite/sessions').rglob('*.json'))]:
        assert path.read_bytes()==(destination/path.relative_to(source)).read_bytes()


def test_export_rebinds_hosted_parent_metadata_and_refuses_changed_telemetry(tmp_path):
    source,destination=tmp_path/'source',tmp_path/'exported'
    fixture_run(source)
    endpoint='https://service.example.run.app'
    # Rebuild parent bindings in chronological order after adding endpoint metadata.
    previous=None
    for name in ('history/first.manifest.json','history/second.manifest.json','manifest.json'):
        data=read(source/name)
        data['execution']={'mode':'hosted','endpoint':endpoint}
        if previous:data['parent']['manifest_sha256']=digest(source/previous)
        save(source/name,data);previous=name
    export_evidence.export(source,destination)
    verify_lineage(destination,read(destination/'manifest.json'))
    for name in ('history/first.manifest.json','history/second.manifest.json','manifest.json'):
        assert read(destination/name)['execution']['endpoint']=='<hosted-endpoint>'
    raw=source/'suite/sessions/case/turn_0.json';save(raw,{'events':[]})
    data=read(source/'manifest.json');data['telemetry_sha256']={str(raw.relative_to(source)):digest(raw)}
    save(source/'manifest.json',data);raw.write_text('{}')
    with pytest.raises(ValueError,match='hash-mismatched telemetry'):
        export_evidence.export(source,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()
