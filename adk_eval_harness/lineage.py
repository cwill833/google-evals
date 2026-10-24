"""Verify that recovery extends immutable evidence rather than replacing failed attempts."""
import copy
from pathlib import Path
from .gate import read
from .attempts import artifact, require


def normalized(attempt):
    value = copy.deepcopy(attempt)
    command = value.get('command', {})
    if command.get('exit_code') is None:
        command['status'] = 'interrupted'
    return value


def verify_lineage(run, manifest):
    run = Path(run).resolve()
    seen = set()
    child = manifest
    while child.get('parent') is not None:
        link = child['parent']
        require(isinstance(link, dict), 'invalid recovery parent link')
        path = artifact(run, link, 'manifest')
        require(path not in seen, 'cyclic recovery lineage')
        seen.add(path)
        if 'gate' in link:
            artifact(run, link, 'gate')
        parent = read(path)
        require(parent.get('schema_version') == 3 and parent.get('generation_complete') is True,
                'parent generation incomplete or unsupported')
        require(not parent.get('errors'), 'recovery parent contains fatal errors')
        for key in ('config_sha256', 'policy_sha256', 'source_sha256', 'agent', 'versions', 'commands', 'plan', 'execution', 'telemetry_sha256'):
            require(child.get(key) == parent.get(key), f'recovery changed original {key}')
        require(set(child['suites']) == set(parent['suites']), 'recovery changed suite coverage')
        for suite, original in parent['suites'].items():
            current = child['suites'][suite]
            require(current.get('trace') == original.get('trace')
                    and current.get('trace_sha256') == original.get('trace_sha256'),
                    'recovery changed original trace')
            prefix = current.get('attempts', [])[:len(original.get('attempts', []))]
            require([normalized(a) for a in prefix] == [normalized(a) for a in original.get('attempts', [])],
                    'recovery changed or dropped original grading attempts')
        child = parent
