import hashlib
import json

import pytest

from scripts.deploy_hosted import deployment_sources, stage_sources
from scripts.hosted_manifest import build_manifest


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'checkout'
    for name in ('pyproject.toml', 'uv.lock', '.python-version', 'examples/load/agent.py',
                 'examples/support/agent.py', 'deployment/hosted/server.py',
                 'deployment/hosted/Dockerfile', 'deployment/hosted/cloudbuild.yaml'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture ' + name)
    return root


def test_stage_excludes_secrets_and_refuses_reuse(source, tmp_path):
    (source / 'examples/load/.env').write_text('PRIVATE')
    stage = tmp_path / 'stage'
    stage_sources(stage, source)
    assert not (stage / 'examples/load/.env').exists()
    (stage / '.env').write_text('STALE PRIVATE')
    with pytest.raises(FileExistsError):
        stage_sources(stage, source)


def test_stage_rejects_symlinks_before_copy(source, tmp_path):
    (source / 'examples/load/secret.py').symlink_to(source / 'uv.lock')
    stage = tmp_path / 'stage'
    with pytest.raises(ValueError, match='symlink'):
        stage_sources(stage, source)
    assert not stage.exists()


def deployed(source, tmp_path):
    stage_sources(tmp_path / 'source', source)
    endpoint = tmp_path / 'endpoint.json'
    endpoint.write_text(json.dumps({'revision': 'service-00001-test', 'url': 'https://fixture.test'}))
    metadata = {'schema_version': 1, 'agents': {'load': {}},
                'source_sha256': {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                                  for name in deployment_sources(source)},
                'runtime': {'platform': 'local'}, 'deployment_revision': 'local'}
    return endpoint, metadata


def test_manifest_pins_revision_excludes_runtime_and_requires_new_output(source, tmp_path, monkeypatch):
    monkeypatch.setenv('GOOGLE_GENAI_USE_VERTEXAI', 'false')
    endpoint, metadata = deployed(source, tmp_path)
    output = tmp_path / 'manifest.json'
    def factory():
        import os
        assert os.environ['GOOGLE_GENAI_USE_VERTEXAI'] == 'true'
        return metadata
    result = build_manifest(endpoint, output, root=source, metadata_factory=factory)
    assert result['deployment_revision'] == 'service-00001-test'
    assert 'runtime' not in result
    assert json.loads(output.read_text()) == result
    with pytest.raises(ValueError, match='new'):
        build_manifest(endpoint, output, root=source, metadata_factory=factory)


@pytest.mark.parametrize('change', ['bytes', 'added', 'metadata'])
def test_manifest_refuses_drift(source, tmp_path, change):
    endpoint, metadata = deployed(source, tmp_path)
    if change == 'bytes':
        (source / 'examples/load/agent.py').write_text('changed')
    elif change == 'added':
        (source / 'examples/load/new.py').write_text('new')
    else:
        metadata['source_sha256']['examples/load/agent.py'] = '0' * 64
    output = tmp_path / 'manifest.json'
    with pytest.raises(ValueError, match='staging'):
        build_manifest(endpoint, output, root=source, metadata_factory=lambda: metadata)
    assert not output.exists()
