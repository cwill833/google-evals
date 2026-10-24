"""One raw-evidence integrity contract for gating, reporting and recovery."""
import hashlib
from pathlib import Path
import re


def verify_telemetry(run, manifest):
    """Return the public integrity verdict and only verified artifact paths.

    Historical manifests without bindings remain explicitly unbound. Once a run
    declares bindings, missing, changed and additional session JSON files fail.
    """
    run = Path(run).resolve()
    hashes = manifest.get('telemetry_sha256')
    unbound = 'telemetry_sha256' not in manifest
    result = {'binding': 'unbound_historical' if unbound else 'manifest_sha256',
              'errors': [], 'note': (
                  'Unbound historical telemetry: raw telemetry was not hashed by the original run manifest.'
                  if unbound else
                  'Raw session telemetry and state files are checked against original manifest hashes.')}
    checked = {}
    if unbound:
        return result, checked
    if not isinstance(hashes, dict):
        result['errors'].append('Telemetry hash manifest is malformed')
        return result, checked
    for relative, expected in hashes.items():
        try:
            if not isinstance(relative, str) or Path(relative).is_absolute():
                raise ValueError('artifact path must be relative')
            path = (run / relative).resolve()
            if not path.is_relative_to(run):
                raise ValueError('artifact path escapes run')
            if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
                raise ValueError('invalid SHA256')
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('hash mismatch')
            checked[relative] = path
        except (ValueError, OSError) as exc:
            result['errors'].append(f'Telemetry integrity failure for {relative}: {exc}')
    for path in sorted(run.glob('*/sessions/**/*.json')):
        relative = str(path.relative_to(run))
        if relative not in hashes:
            result['errors'].append(f'Telemetry integrity failure for {relative}: missing manifest hash')
    return result, checked
