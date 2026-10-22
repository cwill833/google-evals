"""Build offline expected metadata from the exact staged Cloud Run sources.

Run with --endpoint <deployment-output>/endpoint.json --output <new-manifest.json>.
This validates the retained source staging directory; it does not contact Cloud Run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deploy_hosted import deployment_sources


def build_manifest(endpoint, output, *, root=ROOT, metadata_factory=None):
    endpoint, output = Path(endpoint), Path(output)
    if output.exists():
        raise ValueError('Output must be new')
    deployed = json.loads(endpoint.read_text())
    revision = deployed.get('revision')
    if not isinstance(revision, str) or not revision or revision == 'local':
        raise ValueError('Endpoint must name a deployed revision')
    stage = endpoint.parent / 'source'
    names = deployment_sources(root)
    if deployment_sources(stage) != names:
        raise ValueError('Current source file set differs from deployed staging')
    for name in names:
        if (root / name).read_bytes() != (stage / name).read_bytes():
            raise ValueError('Current source differs from deployed staging: ' + name)
    # ADK function response schemas depend on this mode. Set it before imports.
    os.environ['GOOGLE_GENAI_USE_VERTEXAI'] = 'true'
    if metadata_factory is None:
        from deployment.hosted.server import metadata
        metadata_factory = metadata
    observed = metadata_factory()
    for name, digest in observed['source_sha256'].items():
        if name not in names or hashlib.sha256((stage / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Agent metadata does not match deployed staging: ' + name)
    result = {key: observed[key] for key in ('schema_version', 'agents', 'source_sha256')}
    result['deployment_revision'] = revision
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as handle:
        handle.write(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build_manifest(args.endpoint, args.output)
    print('Wrote revision-pinned deployment manifest after staged source verification')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
