"""Clean Linux/public-ZIP verification of hosted ADK transport and native-grade recovery.

Inputs are mounted at /input/reference.zip, /input/endpoint.json and
/input/deployment-manifest.json. Credentials and gcloud are supplied by the
execution environment. Logs and detailed evidence stay under /verification.
Agent turns are never retried after generation or grading failures.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = Path('/verification')
INPUT = Path('/input')
sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def package_check():
    """Verify the executing checkout is the supplied public package, not local edits."""
    count = 0
    with zipfile.ZipFile(INPUT/'reference.zip') as archive:
        seen = set()
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename)
            if (not name.parts or name.parts[0] != 'google-agent-evals-demo'
                    or '..' in name.parts or name.is_absolute() or info.filename in seen):
                raise ValueError('Invalid or duplicate reference archive path')
            seen.add(info.filename)
            path = ROOT.joinpath(*name.parts[1:])
            if not path.is_file() or path.read_bytes() != archive.read(info):
                raise ValueError('Executing package differs from reference archive: ' + str(name))
            count += 1
    if count == 0 or 'google-agent-evals-demo/scripts/linux_verify_hosted.py' not in seen:
        raise ValueError('Reference archive does not include this verifier')
    return {'input_archive_sha256':digest(INPUT/'reference.zip'), 'verified_archive_files':count}


def execute(name, argv, *, timeout=1800):
    started = time.monotonic()
    with (OUT/(name+'.log')).open('w') as log:
        code = subprocess.call(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    result = {'exit_code':code, 'elapsed_seconds':time.monotonic()-started, 'passed':code == 0}
    print(f'{name}: exit {code}', flush=True)
    return result


def record(report, name, value):
    report['steps'][name] = value
    save(OUT/'verification.json', report)


def gate_summary(run):
    manifest = read(run/'manifest.json') if (run/'manifest.json').is_file() else {}
    requested = manifest.get('plan', {}).get('native_grade_count', 0)
    if not (run/'gate.json').is_file():
        return {'evaluation_complete':False, 'release_gate_passed':False, 'native_grade_count':0,
                'requested_grade_count':requested, 'scores':[], 'errors':['No completed gate report'], 'warnings':[]}
    gate = read(run/'gate.json')
    # Schema-3 effective rows contain unchanged selected valid scores; unavailable
    # native grades are null placeholders, not completed grading observations.
    completed = sum(type(row.get('score')) in (int, float) and math.isfinite(row['score'])
                    and 0 <= row['score'] <= 1 for row in gate.get('scores', []))
    return {'evaluation_complete':gate.get('evaluation_complete') is True,
            'release_gate_passed':gate.get('passed') is True, 'native_grade_count':completed,
            'requested_grade_count':requested,
            'scores':gate.get('scores', []), 'errors':gate.get('errors', []), 'warnings':gate.get('warnings', []),
            'recovered_grading_failures':gate.get('recovered_grading_failures', 0)}


def session_evidence(run):
    """Hash original raw turns, state, deployment metadata and telemetry together."""
    return {str(path.relative_to(run)):digest(path) for path in sorted(run.glob('*/sessions/**/*.json'))}


def remote_turns(run):
    """Each adapter's counters are cumulative per case; count only its last turn."""
    cases, total, measured_turns = 0, 0, 0
    for folder in sorted(run.glob('*/sessions/*')):
        if not folder.is_dir():
            continue
        turns = sorted(folder.glob('turn_*.json'), key=lambda p:int(p.stem.split('_')[1]))
        if not turns:
            continue
        snapshots = [read(path) for path in turns]
        for item in snapshots:
            telemetry = item.get('telemetry', {})
            if telemetry.get('status') != 'success' or type(telemetry.get('duration_seconds')) not in (int, float):
                raise ValueError('Missing or unsuccessful hosted turn telemetry')
        posts = snapshots[-1].get('transport_stats_after_turn', {}).get('run_posts')
        if type(posts) is not int or posts != len(turns):
            raise ValueError('Remote run counter differs from executed turn count')
        cases += 1
        measured_turns += len(turns)
        total += posts
    return {'cases':cases, 'measured_turns':measured_turns, 'remote_run_posts':total}


def generation_count(manifest):
    return sum('generate' in command.get('argv', []) for command in manifest.get('commands', []))


def hosted_config(config, url):
    config.update(project_root=str(ROOT), turn_timeout=180, qps=.5,
                  grading_queue=str(ROOT/'artifacts/hosted-queue'), queue_budget_qps=.5,
                  queue_cooldown_seconds=30, grading_batch_size=6, max_grading_attempts=3,
                  grading_timeout=3600, hosted={'url':url, 'auth':'gcloud',
                                             'deployment_manifest':str(INPUT/'deployment-manifest.json')})
    return config


def verify_load(url):
    from scripts.load_test import prepare
    from scripts.queue_load_test import monitored, pending_pairs
    output = OUT/'load'
    output.mkdir(exist_ok=False)
    prepare(output/'pack', 12, turns=3, payload_bytes=128, qps=.5,
            native_metric='multi_turn_task_success')
    config_path = output/'pack/config.json'
    config = hosted_config(read(config_path), url)
    config['app_name'] = 'load'
    save(config_path, config)
    first = output/'initial'
    first.mkdir()
    print('hosted_load: generating 12 remote conversations; interrupting only the first native grading process', flush=True)
    initial_status = monitored([sys.executable, '-m', 'adk_eval_harness', 'run', '--config', str(config_path),
                               '--output', str(first/'run')], first, interrupt_first_grade=True, timeout=3600)
    initial = first/'run'
    summary = {'mode':'hosted_adk_google_native_grading_with_grader_only_interruption',
               'cases':12, 'turns':36, 'initial':initial_status, 'initial_gate':gate_summary(initial)}
    save(output/'summary.json', summary)
    if not (initial/'manifest.json').is_file():
        summary.update(passed=False, error='Initial manifest unavailable; no agent retry attempted')
        save(output/'summary.json', summary)
        return summary
    original_manifest = read(initial/'manifest.json')
    original_manifest_hash = digest(initial/'manifest.json')
    trace_hash = digest(initial/'load/traces.json')
    evidence_hashes = session_evidence(initial)
    counts = remote_turns(initial)
    pending = pending_pairs(initial)
    summary.update(initial_pending_pairs=[list(pair) for pair in sorted(pending)], remote_execution=counts,
                   original_trace_sha256=trace_hash, original_session_evidence_sha256=evidence_hashes)
    if not pending:
        summary.update(passed=False, error='No pending native grades; interruption/recovery scenario was not exercised')
        save(output/'summary.json', summary)
        return summary
    recovery = output/'recovery'
    recovery.mkdir()
    print(f'hosted_load: recovering {len(pending)} unavailable native grades without agent execution', flush=True)
    recovery_status = monitored([sys.executable, '-m', 'adk_eval_harness', 'recover', '--config', str(config_path),
                                '--from-run', str(initial), '--output', str(recovery/'run')], recovery, timeout=3600)
    recovered = recovery/'run'
    summary['recovery'] = recovery_status
    summary['recovery_gate'] = gate_summary(recovered)
    if not (recovered/'manifest.json').is_file():
        summary.update(passed=False, error='Recovery manifest unavailable; no agent retry attempted')
        save(output/'summary.json', summary)
        return summary
    recovered_manifest = read(recovered/'manifest.json')
    original_attempts = len(original_manifest['suites']['load']['attempts'])
    new_attempts = recovered_manifest['suites']['load']['attempts'][original_attempts:]
    requested_pairs = [('load', case['eval_case_id'], attempt['metric']) for attempt in new_attempts
                       for case in read(recovered/attempt['trace'])['eval_cases']]
    remaining = pending_pairs(recovered)
    checks = {
        'only_native_grader_interrupted':initial_status['injected_interruption'] is not None,
        'generation_completed_before_grading':original_manifest.get('generation_complete') is True,
        'twelve_remote_cases':counts['cases'] == 12,
        'exactly_36_remote_run_posts':counts['remote_run_posts'] == counts['measured_turns'] == 36,
        'source_manifest_unchanged':digest(initial/'manifest.json') == original_manifest_hash,
        'source_trace_unchanged':digest(initial/'load/traces.json') == trace_hash,
        'recovery_trace_identical':digest(recovered/'load/traces.json') == trace_hash,
        'source_session_evidence_unchanged':session_evidence(initial) == evidence_hashes,
        'recovery_session_evidence_identical':session_evidence(recovered) == evidence_hashes,
        'recovery_requested_exact_pending_pairs':len(requested_pairs) == len(pending) and set(requested_pairs) == pending,
        'no_generation_repeated':generation_count(original_manifest) == generation_count(recovered_manifest) == 1,
        'no_new_remote_run_posts':remote_turns(recovered) == counts,
        'native_coverage_complete':not remaining and summary['recovery_gate']['evaluation_complete'],
    }
    summary.update(checks=checks, remaining_pending_pairs=[list(pair) for pair in sorted(remaining)],
                   platform_checks_passed=all(checks.values()),
                   passed=all(checks.values()) and summary['recovery_gate']['release_gate_passed'])
    save(output/'summary.json', summary)
    print(f"hosted_load: complete={summary['recovery_gate']['evaluation_complete']}; policy_pass={summary['recovery_gate']['release_gate_passed']}", flush=True)
    return summary


def support_pack_config(url):
    pack = ROOT/'packs/support'
    config = read(pack/'config.json')
    for field in ('policy', 'freeze'):
        config[field] = str((pack/config[field]).resolve())
    for suite in config['suites'].values():
        for field in ('dataset', 'metrics'):
            suite[field] = str((pack/suite[field]).resolve())
    config = hosted_config(config, url)
    config['app_name'] = 'support'
    return config


def verify_support(url):
    from scripts.queue_load_test import monitored
    output = OUT/'support'
    output.mkdir(exist_ok=False)
    config = support_pack_config(url)
    config_path = output/'config.json'
    save(config_path, config)
    print('hosted_support: evaluating the second ADK agent with its frozen pack', flush=True)
    process = monitored([sys.executable, '-m', 'adk_eval_harness', 'run', '--config', str(config_path),
                         '--output', str(output/'run')], output, timeout=3600)
    run = output/'run'
    summary = {'mode':'hosted_second_adk_agent_original_frozen_datasets_and_policy',
               'process':process, **gate_summary(run)}
    if (run/'manifest.json').is_file():
        manifest = read(run/'manifest.json')
        summary['generation_complete'] = manifest.get('generation_complete') is True
        if summary['generation_complete']:
            summary['remote_execution'] = remote_turns(run)
            counts, plan = summary['remote_execution'], manifest.get('plan', {})
            summary['platform_checks_passed'] = (
                counts['cases'] == plan.get('case_count')
                and counts['remote_run_posts'] == counts['measured_turns'] == plan.get('turn_count')
                and summary['native_grade_count'] == plan.get('native_grade_count')
                and summary['evaluation_complete'])
    summary['passed'] = (process['exit_code'] == 0 and summary['release_gate_passed']
                         and summary.get('platform_checks_passed') is True)
    save(output/'summary.json', summary)
    print(f"hosted_support: complete={summary['evaluation_complete']}; policy_pass={summary['release_gate_passed']}", flush=True)
    return summary


def installed(report):
    if Path(sys.prefix).resolve() != (ROOT/'.venv').resolve():
        raise RuntimeError('Installed phase must run in the newly created package virtualenv')
    if not report['steps'].get('locked_install', {}).get('passed'):
        raise RuntimeError('Installed phase requires successful locked bootstrap installation')
    if not shutil.which('gcloud'):
        raise RuntimeError('gcloud is required for authenticated hosted verification')
    endpoint = read(INPUT/'endpoint.json')
    url = endpoint.get('url')
    if not isinstance(url, str) or not url.startswith('https://'):
        raise ValueError('endpoint.json must contain an HTTPS url')
    report['input_endpoint_sha256'] = digest(INPUT/'endpoint.json')
    report['deployment_manifest_sha256'] = digest(INPUT/'deployment-manifest.json')
    probe_process = execute('probe', [sys.executable, 'scripts/verify_hosted.py', '--url', url,
        '--deployment-manifest', str(INPUT/'deployment-manifest.json'), '--output', str(OUT/'probe.json')])
    probe = read(OUT/'probe.json') if (OUT/'probe.json').is_file() else {'passed':False, 'checks':{}}
    record(report, 'probe', {'process':probe_process, 'checks':probe.get('checks', {}),
                             'passed':probe_process['passed'] and probe.get('passed') is True})
    if not report['steps']['probe']['passed']:
        return
    for name, operation in [('load', verify_load), ('support', verify_support)]:
        try:
            record(report, name, operation(url))
        except Exception as exc:
            record(report, name, {'passed':False, 'error_class':type(exc).__name__,
                                  'error':str(exc), 'agent_retry_attempted':False})
            print(f'{name}: failed ({type(exc).__name__}); evidence preserved', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if platform.system() != 'Linux':
        raise RuntimeError('Linux required')
    for path in (OUT/'load', OUT/'support', OUT/'probe.json'):
        if path.exists():
            raise ValueError('Verification output must be new: ' + str(path))
    OUT.mkdir(parents=True, exist_ok=True)
    package = package_check()
    if args.installed:
        report = read(OUT/'verification.json')
        if report['input_archive_sha256'] != package['input_archive_sha256']:
            raise ValueError('Reference archive changed between bootstrap and execution')
    else:
        if (OUT/'verification.json').exists() or (ROOT/'.venv').exists():
            raise ValueError('Fresh verification output and a clean package without .venv are required')
        report = {'started_at':datetime.now(timezone.utc).isoformat(), 'platform':platform.platform(),
                  'machine':platform.machine(), 'python':sys.version, 'steps':{}, **package,
                  'scope':'Linux client against authenticated hosted ADK; native Google grading unchanged'}
        save(OUT/'verification.json', report)
    try:
        if not args.installed:
            for name, argv in [
                ('install_uv', [sys.executable, '-m', 'pip', 'install', 'uv==0.10.0']),
                ('locked_install', ['uv', 'sync', '--locked', '--python', sys.executable]),
                ('unit_tests', [str(ROOT/'.venv/bin/python'), '-m', 'pytest', '-q']),
            ]:
                result = execute(name, argv)
                record(report, name, result)
                if not result['passed']:
                    raise RuntimeError(name + ' failed')
            return subprocess.call([str(ROOT/'.venv/bin/python'), __file__, '--installed'], cwd=ROOT)
        installed(report)
        report['passed'] = all(report['steps'].get(name, {}).get('passed') is True
                               for name in ['install_uv', 'locked_install', 'unit_tests', 'probe', 'load', 'support'])
    except Exception as exc:
        report.update(passed=False, error_class=type(exc).__name__, error=str(exc))
        print(f'verification: failed ({type(exc).__name__}); inspect private logs', flush=True)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    save(OUT/'verification.json', report)
    print(json.dumps({'passed':report['passed'], 'steps':{name:step.get('passed') for name,step in report['steps'].items()}}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
