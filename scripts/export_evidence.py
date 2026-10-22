"""Export evidence without rewriting native inputs, grades or evaluator reasoning.

Only manifests, historical gate reports and logs may redact local metadata. Derived
HTML/JSON reports are regenerated from the exported evidence. Private
identifiers in native evidence cause refusal, not a silent score/trace rewrite.
Schema-3 parent snapshots are retained and their metadata hashes rebound bottom-up.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXTENSIONS = {'.json', '.jsonl', '.txt', '.log', '.html', '.yaml', '.yml'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def export(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_dir():
        raise ValueError('Evidence source must be an existing directory')
    if destination.exists():
        raise ValueError('Destination must be new')
    if destination.is_relative_to(source):
        raise ValueError('Export destination must be outside the source run')
    config = dotenv_values(ROOT / '.env')
    replacements = [(str(ROOT), '<workspace>'), (str(Path.home()), '<home>')]
    project = config.get('GOOGLE_CLOUD_PROJECT')
    if project:
        replacements.append((project, '<evaluation-project>'))
    replacements = [(old.encode(), new.encode()) for old, new in replacements]
    regenerate_report = any((source / name).is_file() for name in ('report.json', 'report.html'))
    files = {}
    for path in source.rglob('*'):
        if path.is_file() and path.suffix in EXTENSIONS:
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError('Evidence export does not follow symlinked files')
            name = path.relative_to(source).as_posix()
            if name not in {'report.json', 'report.html'}:
                files[name] = path
    manifests = {name for name in files if name == 'manifest.json' or name.endswith('.manifest.json')}
    gates = {name for name in files if name == 'gate.json' or name.endswith('.gate.json')}
    protected = set(files) - manifests - gates - {name for name,p in files.items() if p.suffix in {'.log','.txt'}}
    documents = {name: json.loads(files[name].read_text()) for name in manifests}
    for document in documents.values():
        execution = document.get('execution') or {}
        if execution.get('mode') == 'hosted' and isinstance(execution.get('endpoint'), str) and execution['endpoint']:
            replacements.append((execution['endpoint'].encode(), b'<hosted-endpoint>'))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)

    def reference(container, key):
        value = container.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f'Missing evidence path: {key}')
        target = (source / value).resolve()
        if not target.is_relative_to(source):
            raise ValueError(f'Evidence path escapes run: {key}')
        relative = target.relative_to(source).as_posix()
        if relative not in files or container.get(key + '_sha256') != _sha(files[relative].read_bytes()):
            raise ValueError(f'Missing or hash-mismatched evidence artifact: {key}')
        container[key] = relative
        return relative

    # Validate bindings before writing. Unfinished attempts may omit results;
    # every artifact that is declared must exist with its original bytes.
    for document in documents.values():
        for name, expected_hash in document.get('telemetry_sha256', {}).items():
            if name not in files or _sha(files[name].read_bytes()) != expected_hash:
                raise ValueError('Missing or hash-mismatched telemetry during evidence export')
            protected.add(name)
        for entry in document.get('suites', {}).values():
            for item in [entry, *entry.get('attempts', [])]:
                for key in ('trace', 'results', 'metric_config'):
                    if key in item:
                        protected.add(reference(item, key))
                    elif key + '_sha256' in item:
                        raise ValueError(f'Artifact hash has no path: {key}')
        parent = document.get('parent')
        if parent:
            name = reference(parent, 'manifest')
            if name not in documents:
                raise ValueError('Parent must reference a retained manifest snapshot')
            if 'gate' in parent:
                if reference(parent, 'gate') not in gates:
                    raise ValueError('Parent must reference a retained gate snapshot')
        if document.get('policy_sha256'):
            if 'policy.json' not in files or document['policy_sha256'] != _sha(files['policy.json'].read_bytes()):
                raise ValueError('Policy hash mismatch during evidence export')
    for name in protected:
        data = files[name].read_bytes()
        if any(old in data for old, _ in replacements):
            raise ValueError(f'Private identifier in immutable evidence: {name}; export refused')

    def redact(data):
        for old, new in replacements:
            data = data.replace(old, new)
        return data

    rendered = {name: redact(files[name].read_bytes()) for name in gates}
    visiting = set()

    def render_manifest(name):
        if name in rendered:
            return rendered[name]
        if name in visiting:
            raise ValueError('Cyclic evidence parent lineage')
        visiting.add(name)
        document = copy.deepcopy(documents[name])
        if document.get('parent'):
            parent = document['parent']
            parent['manifest_sha256'] = _sha(render_manifest(parent['manifest']))
            if 'gate' in parent:
                parent['gate_sha256'] = _sha(rendered[parent['gate']])
        previous_export = document.get('export')
        document['export'] = {
            'source': 'private originals retained locally',
            'redactions': ['workspace absolute prefix', 'home directory prefix', 'configured Cloud project identifier'],
            'raw_manifest_sha256': _sha(files[name].read_bytes()),
            'native_artifacts': 'byte-identical; export refuses private identifiers in native evidence',
            'lineage_metadata': 'parent manifest/gate hashes rebound to exported snapshots',
        }
        if document.get('execution', {}).get('mode') == 'hosted':
            document['export']['redactions'].append('hosted endpoint URL')
        if previous_export:
            document['export']['previous_export'] = previous_export
        rendered[name] = redact((json.dumps(document, indent=2) + '\n').encode())
        visiting.remove(name)
        return rendered[name]

    for name in manifests:
        render_manifest(name)
    destination.mkdir(parents=True)
    for name, path in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name in rendered:
            data = rendered[name]
        elif name in protected:
            data = path.read_bytes()
        else:
            data = redact(path.read_bytes())
        target.write_bytes(data)
    if regenerate_report:
        # Derived views must describe the exported manifest and its rebound lineage,
        # not carry over private paths or a stale verdict from the source report.
        from adk_eval_harness.reporting import write_report
        write_report(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    export(args.source, args.destination)
