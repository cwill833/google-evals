"""Trusted local ADK project configuration. Paths are relative to the config file."""
import importlib
import math
import os
from pathlib import Path
import re
import sys
from .gate import read, digest

CONFIG_KEYS = {
    'schema_version', 'framework', 'project_root', 'agent', 'agent_factory',
    'app_name', 'user_id', 'source_roots', 'policy', 'suites', 'freeze',
    'runner_factory', 'initial_state', 'turn_timeout', 'command_timeout', 'qps',
    'generation_timeout', 'grading_timeout', 'session_timeout', 'cleanup_timeout', 'shutdown_grace',
    'grading_queue', 'queue_budget_qps', 'queue_wait_timeout', 'queue_cooldown_seconds',
    'grading_batch_size', 'max_grading_attempts',
    'hosted', 'case_concurrency',
}


def import_reference(reference):
    if not isinstance(reference, str) or not re.fullmatch(
        r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', reference
    ):
        raise ValueError('Expected module:attribute import reference')
    return reference.split(':')


def symbol(reference):
    module, name = import_reference(reference)
    return getattr(importlib.import_module(module), name)


def nonempty_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')
    return value


def load(path):
    path = Path(path).resolve()
    config = read(path)
    if not isinstance(config, dict):
        raise ValueError('Configuration must be a JSON object')
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        raise ValueError('Unknown configuration fields: ' + ', '.join(sorted(unknown)))
    if type(config.get('schema_version')) is not int or config['schema_version'] != 1 or config.get('framework') != 'adk':
        raise ValueError('Only schema 1 Python ADK configurations are supported')
    root = (path.parent / nonempty_string(config.get('project_root'), 'project_root')).resolve()
    if not root.is_dir():
        raise ValueError('Missing ADK project root')
    config['_path'], config['_root'] = path, root
    config['policy'] = (path.parent / nonempty_string(config.get('policy'), 'policy')).resolve()
    if not config['policy'].is_file():
        raise ValueError('Missing policy file')
    import_reference(config.get('agent'))
    for key in ('app_name', 'user_id'):
        nonempty_string(config.get(key), key)
    if 'agent_factory' in config and type(config['agent_factory']) is not bool:
        raise ValueError('agent_factory must be a boolean')
    if 'runner_factory' in config:
        import_reference(config['runner_factory'])
    if 'hosted' in config:
        from urllib.parse import urlsplit
        hosted = config['hosted']
        if not isinstance(hosted, dict) or set(hosted) != {'url', 'auth', 'deployment_manifest'}:
            raise ValueError('hosted requires exactly url, auth, deployment_manifest')
        url = urlsplit(nonempty_string(hosted['url'], 'hosted.url'))
        local = url.hostname in ('localhost', '127.0.0.1', '::1')
        if (not url.hostname or url.username or url.password or url.query or url.fragment
            or url.scheme not in ('http', 'https') or (url.scheme != 'https' and not local)):
            raise ValueError('Hosted URL must use HTTPS without credentials/query (HTTP only for loopback tests)')
        if hosted['auth'] not in ('gcloud', 'none') or (hosted['auth'] == 'none' and not local):
            raise ValueError('Remote hosted agents require gcloud identity authentication')
        if 'runner_factory' in config:
            raise ValueError('hosted and runner_factory are mutually exclusive')
        hosted['deployment_manifest'] = (path.parent / nonempty_string(hosted['deployment_manifest'], 'deployment_manifest')).resolve()
        if not hosted['deployment_manifest'].is_file():
            raise ValueError('Missing expected deployment manifest')
    if 'initial_state' in config and not isinstance(config['initial_state'], dict):
        raise ValueError('initial_state must be an object')
    for key in ('turn_timeout', 'session_timeout', 'cleanup_timeout', 'shutdown_grace',
                'command_timeout', 'generation_timeout', 'grading_timeout', 'qps',
                'queue_budget_qps', 'queue_wait_timeout'):
        if key in config and (type(config[key]) not in (int, float) or not math.isfinite(config[key]) or config[key] <= 0):
            raise ValueError(f'{key} must be a finite positive number')
    if 'queue_cooldown_seconds' in config and (type(config['queue_cooldown_seconds']) not in (int, float)
            or not math.isfinite(config['queue_cooldown_seconds']) or config['queue_cooldown_seconds'] < 0):
        raise ValueError('queue_cooldown_seconds must be finite and nonnegative')
    for key in ('grading_batch_size', 'max_grading_attempts', 'case_concurrency'):
        if key in config and (type(config[key]) is not int or config[key] < 1):
            raise ValueError(f'{key} must be a positive integer')
    if config.get('case_concurrency', 1) > 1 and not config.get('hosted') and not config.get('agent_factory'):
        raise ValueError('Concurrent local execution requires agent_factory with fresh agents')
    if 'grading_queue' in config:
        config['grading_queue'] = (path.parent / nonempty_string(config['grading_queue'], 'grading_queue')).resolve()
    sources = config.get('source_roots')
    if not isinstance(sources, list) or not sources:
        raise ValueError('source_roots must be a nonempty list of project-relative source paths')
    for name in sources:
        nonempty_string(name, 'source_roots entry')
        target = (root / name).resolve()
        if Path(name).is_absolute() or not target.is_relative_to(root):
            raise ValueError('source_roots entries must remain within project_root: ' + name)
        if not target.exists():
            raise ValueError('Missing declared source: ' + name)
    if not isinstance(config.get('suites'), dict):
        raise ValueError('suites must be an object')
    for suite, spec in config['suites'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', suite):
            raise ValueError('Invalid suite name: ' + suite)
        if not isinstance(spec, dict) or set(spec) != {'dataset', 'metrics'}:
            raise ValueError(f'{suite}: suite must contain exactly dataset and metrics')
        for key in ('dataset', 'metrics'):
            spec[key] = (path.parent / nonempty_string(spec[key], f'{suite}.{key}')).resolve()
            if not spec[key].is_file():
                raise ValueError(f'{suite}: missing {key} file')
    if 'freeze' in config:
        freeze_path = (path.parent / nonempty_string(config['freeze'], 'freeze')).resolve()
        freeze = read(freeze_path)
        if not isinstance(freeze, dict) or not isinstance(freeze.get('sha256'), dict) or not freeze['sha256']:
            raise ValueError('Freeze manifest requires nonempty sha256 entries')
        for name, expected in freeze['sha256'].items():
            if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
                raise ValueError('Invalid frozen SHA256: ' + name)
            if digest((freeze_path.parent / name).resolve()) != expected:
                raise ValueError('Frozen evaluation inputs changed: ' + name)
    sys.path.insert(0, str(root))
    return config


def source_hashes(config):
    paths = {config['_path'], config['policy']}
    if config.get('hosted'):
        paths.add(config['hosted']['deployment_manifest'])
    for suite in config['suites'].values():
        paths.update([suite['dataset'], suite['metrics']])
    roots = [('project', config['_root'], name) for name in config['source_roots']]
    roots.append(('harness', Path(__file__).resolve().parent.parent, 'adk_eval_harness'))
    hashes = {}
    for namespace, base, name in roots:
        target = (base / name).resolve()
        if not target.exists():
            raise ValueError('Missing declared source: ' + name)
        for file in target.rglob('*') if target.is_dir() else [target]:
            # Explicit files (including dependency dotfiles) are always hashed.
            relative = file.relative_to(target) if target.is_dir() else Path(file.name)
            if file.is_file() and (not target.is_dir() or not any(x.startswith('.') or x == '__pycache__' for x in relative.parts)):
                hashes[f'{namespace}/{file.relative_to(base)}'] = digest(file)
    for file in paths:
        hashes['inputs/' + os.path.relpath(file, config['_path'].parent)] = digest(file)
    return hashes
