"""External pack errors fail before agent import, inference or grading."""
import json
from pathlib import Path

import pytest

from adk_eval_harness.config import load, source_hashes


def project(tmp_path):
    (tmp_path / 'my_agent.py').write_text('root_agent = None\n')
    (tmp_path / 'policy.json').write_text('{}')
    (tmp_path / 'cases.json').write_text('[]')
    (tmp_path / 'metrics.yaml').write_text('metrics_to_run: [final_response_match]\n')
    return {'schema_version': 1, 'framework': 'adk', 'project_root': '.',
            'agent': 'my_agent:root_agent', 'app_name': 'my_agent', 'user_id': 'test',
            'source_roots': ['my_agent.py'], 'policy': 'policy.json',
            'suites': {'smoke': {'dataset': 'cases.json', 'metrics': 'metrics.yaml'}}}


def write_config(tmp_path, config):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    return path


@pytest.mark.parametrize(('key', 'value', 'error'), [
    ('turn_timout', 1, 'Unknown configuration'),
    ('agent_factory', 'false', 'boolean'),
    ('qps', True, 'positive'),
    ('qps', 0, 'positive'),
    ('turn_timeout', -1, 'positive'),
    ('generation_timeout', '1800', 'positive'),
    ('grading_timeout', 0, 'positive'),
    ('initial_state', [], 'object'),
    ('agent', 'my_agent.root_agent', 'module:attribute'),
    ('source_roots', 'my_agent.py', 'nonempty list'),
    ('source_roots', ['../'], 'within project_root'),
    ('source_roots', ['missing.py'], 'Missing declared source'),
])
def test_invalid_config_has_actionable_error(tmp_path, key, value, error):
    config = project(tmp_path)
    config[key] = value
    with pytest.raises(ValueError, match=error):
        load(write_config(tmp_path, config))


def test_missing_dataset_and_unknown_suite_field_fail_early(tmp_path):
    config = project(tmp_path)
    config['suites']['smoke']['dataset'] = 'missing.json'
    with pytest.raises(ValueError, match='missing dataset'):
        load(write_config(tmp_path, config))
    config['suites']['smoke']['dataset'] = 'cases.json'
    config['suites']['smoke']['metrcs'] = 'metrics.yaml'
    with pytest.raises(ValueError, match='exactly dataset and metrics'):
        load(write_config(tmp_path, config))


def test_external_sources_cannot_overwrite_core_hashes_and_dotfiles_are_bound(tmp_path):
    config = project(tmp_path)
    (tmp_path / 'adk_eval_harness').mkdir()
    (tmp_path / 'adk_eval_harness/config.py').write_text('# different project file\n')
    (tmp_path / '.python-version').write_text('3.12\n')
    config['source_roots'] += ['adk_eval_harness', '.python-version']
    loaded = load(write_config(tmp_path, config))
    hashes = source_hashes(loaded)
    assert hashes['project/adk_eval_harness/config.py'] != hashes['harness/adk_eval_harness/config.py']
    assert 'project/.python-version' in hashes
    (tmp_path / 'adk_eval_harness/config.py').write_text('# changed\n')
    assert source_hashes(loaded) != hashes


@pytest.mark.parametrize('value',[True,0,-1,1.2,'4'])
def test_invalid_case_concurrency(tmp_path,value):
    config=project(tmp_path);config['case_concurrency']=value
    with pytest.raises(ValueError,match='case_concurrency must be a positive integer'):
        load(write_config(tmp_path,config))


def test_concurrent_local_config_requires_factory(tmp_path):
    config=project(tmp_path);config['case_concurrency']=4
    with pytest.raises(ValueError,match='requires agent_factory'):
        load(write_config(tmp_path,config))
    config['agent_factory']=True
    assert load(write_config(tmp_path,config))['case_concurrency']==4
