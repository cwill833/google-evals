"""Configuration-driven execution of Python ADK agents and native Google grading."""
import argparse
import copy
from contextlib import contextmanager
import fcntl
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from .config import load, symbol, source_hashes
from .gate import read, digest, text, evaluate

VERSIONS = {'google-agents-cli':'1.11.0', 'google-adk':'2.12.0', 'google-cloud-aiplatform':'2.2.0'}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


@contextmanager
def writer_lock(output):
    """A native child inherits this lock, so a dead parent cannot trigger a live retry."""
    fd = os.open(output / '.run.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Run is still active; recovery cannot overlap a writer or native child') from exc
        yield fd
    finally:
        os.close(fd)


def command(argv, log, config, records, timeout=1800, *, pass_fds=(), on_update=None):
    core = Path(__file__).resolve().parent.parent
    env = {**os.environ, 'CI':'1', 'AGENTS_CLI_TELEMETRY':'0',
           'PYTHONPATH':os.pathsep.join([str(core), str(config['_root'])])}
    started = time.monotonic()
    record = {'argv':list(map(str, argv)), 'exit_code':None, 'status':'starting',
              'log':log.name, 'timeout_seconds':timeout}
    records.append(record)
    if on_update:
        on_update()
    with log.open('w') as stream:
        try:
            process = subprocess.Popen(record['argv'], cwd=config['_root'], env=env,
                                       stdout=stream, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, start_new_session=True, pass_fds=pass_fds)
        except OSError:
            record.update(exit_code=127, status='launch_failed')
            if on_update:
                on_update()
            raise
        record.update(pid=process.pid, status='running')
        if on_update:
            on_update()
        try:
            code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            grace = config.get('shutdown_grace', 10)
            record['shutdown'] = {'reason':'interrupt' if isinstance(exc, KeyboardInterrupt) else 'timeout',
                                  'signal':'SIGINT', 'grace_seconds':grace, 'forced_group_kill':False}
            try:
                # asyncio.run translates the first SIGINT into task cancellation,
                # allowing workers to save partial evidence and close their runners.
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=grace)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                pass
            finally:
                # Also stop leftover descendants when the leader already exited.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    record['shutdown']['forced_group_kill'] = True
                except ProcessLookupError:
                    pass
            process.wait()
            code = 130 if isinstance(exc, KeyboardInterrupt) else 124
    record.update(exit_code=code, status='completed' if code == 0 else 'failed',
                  elapsed_seconds=time.monotonic() - started)
    if on_update:
        on_update()
    if code == 130:
        raise KeyboardInterrupt('Native command interrupted')
    if code:
        raise RuntimeError(f'Command exited {code}; see {log.name}')


def conversations(path):
    from .validation import dataset_cases
    return dataset_cases(path)


def inspect_agent(config):
    from .generation import agent_metadata
    from dotenv import load_dotenv
    load_dotenv(config['_root'] / '.env', override=False)
    target = symbol(config['agent'])
    agent = target() if config.get('agent_factory', False) else target
    return agent_metadata(agent)


def generate(config, suite, output):
    from .generation import generate as generate_cases
    from google.adk.agents import BaseAgent
    from dotenv import load_dotenv
    load_dotenv(config['_root'] / '.env', override=False)
    target = symbol(config['agent'])
    agent = None if config.get('agent_factory', False) else target
    if agent is not None and not isinstance(agent, BaseAgent):
        raise TypeError('Configured agent must be an ADK BaseAgent')
    factory = symbol(config['runner_factory']) if config.get('runner_factory') else None
    if config.get('hosted'):
        from functools import partial
        from .remote import RemoteRunner
        factory = partial(RemoteRunner, hosted=config['hosted'], turn_timeout=config.get('turn_timeout', 180))
    asyncio.run(generate_cases(conversations(config['suites'][suite]['dataset']), output, agent,
                turn_timeout=config.get('turn_timeout', 180), app_name=config['app_name'],
                user_id=config['user_id'], initial_state=config.get('initial_state', {}), runner_factory=factory,
                agent_factory=target if config.get('agent_factory', False) else None,
                case_concurrency=config.get('case_concurrency', 1), metadata_only_agent=bool(config.get('hosted')),
                session_timeout=config.get('session_timeout', 30), cleanup_timeout=config.get('cleanup_timeout', 5)))


def versions():
    actual = {key:importlib.metadata.version(key) for key in VERSIONS}
    if actual != VERSIONS:
        raise ValueError('Client versions differ from the verified lockfile')
    return actual


def identity(config, manifest):
    if (manifest.get('config_sha256') != digest(config['_path'])
        or manifest.get('policy_sha256') != digest(config['policy'])
        or manifest.get('agent') != config['agent']
        or manifest.get('source_sha256') != source_hashes(config)):
        raise ValueError('Current config, policy, agent or sources do not match evaluated evidence')


def finish(output, manifest):
    if 'telemetry_sha256' not in manifest:
        manifest['telemetry_sha256'] = {str(path.relative_to(output)):digest(path)
            for suite in manifest['plan'].get('per_suite', {})
            for path in (output / suite / 'sessions').rglob('*.json')}
    save(output / 'manifest.json', manifest)
    gate = evaluate(output, output / 'policy.json')
    save(output / 'gate.json', gate)
    from .reporting import write_report
    write_report(output)
    print(json.dumps({'passed':gate['passed'], 'errors':gate['errors'], 'warnings':gate['warnings'],
                      'native_grades':len(gate['scores'])}, indent=2), flush=True)
    return 0 if gate['passed'] else 1


def observed_throttle(log, grades):
    """Look at transport/error messages, never ordinary scores or echoed trace data."""
    if log.exists() and re.search(r'(?:code[=:\s]+429\b|429\s+RESOURCE_EXHAUSTED|429\s+Too Many Requests)',
                                 log.read_text(errors='replace'), re.IGNORECASE):
        return True
    for path in grades.glob('results_*.json'):
        try:
            for row in read(path).get('eval_case_results', []):
                for candidate in row.get('response_candidate_results', []):
                    for value in candidate.get('metric_results', {}).values():
                        message = value.get('error_message')
                        if isinstance(message, str) and re.search(r'\b429\b|\bRESOURCE_EXHAUSTED\b', message):
                            return True
        except (ValueError, TypeError, AttributeError):
            # Malformed native output is rejected by the ledger, never retried here.
            continue
    return False


def grade_pending(config, output, manifest, policy, writer_fd):
    """One attempt per currently unavailable pair; never automatically retry low scores."""
    from .attempts import resolve
    from .grading_queue import GradingQueue
    import yaml
    queue = GradingQueue(config.get('grading_queue', config['_root'] / 'artifacts/grading-queue'),
                         budget_qps=config.get('queue_budget_qps', .5),
                         wait_timeout=config.get('queue_wait_timeout', 3600),
                         cooldown_seconds=config.get('queue_cooldown_seconds', 30))
    cli = Path(sys.executable).parent / 'agents-cli'
    checkpoint = lambda: save(output / 'manifest.json', manifest)
    for suite, entry in manifest['suites'].items():
        _, history, pending = resolve(output, suite, entry, policy['suites'][suite])
        counts = {}
        for item in history:
            for cid in item['case_ids']:
                pair = (cid, item['metric'])
                counts[pair] = counts.get(pair, 0) + 1
        eligible = {pair for pair in pending if counts.get(pair, 0) < config.get('max_grading_attempts', 3)}
        original = read(output / entry['trace'])['eval_cases']
        for metric in policy['suites'][suite]['metrics']:
            cases = [case for case in original if (case['eval_case_id'], metric) in eligible]
            size = config.get('grading_batch_size', 100)
            for start in range(0, len(cases), size):
                subset = cases[start:start + size]
                folder = output / suite / 'attempts' / f'{len(entry["attempts"]):04d}'
                folder.mkdir(parents=True, exist_ok=False)
                trace, metrics, grades = folder / 'traces.json', folder / 'metrics.yaml', folder / 'grades'
                save(trace, {'eval_cases':subset})
                metrics.write_text(yaml.safe_dump({'metrics_to_run':[metric]}))
                grades.mkdir()
                attempt = {'trace':str(trace.relative_to(output)), 'trace_sha256':digest(trace),
                           'metric':metric, 'metric_config':str(metrics.relative_to(output)),
                           'metric_config_sha256':digest(metrics), 'grades_dir':str(grades.relative_to(output)),
                           'status':'queued', 'command':{'exit_code':None, 'status':'interrupted'}}
                entry['attempts'].append(attempt)
                checkpoint()
                records = []
                interrupted = False
                def updated():
                    if records:
                        attempt['command'] = records[-1]
                    checkpoint()
                print(f'{suite}: queueing {len(subset)} cases for native {metric}', flush=True)
                try:
                    with queue.acquire(len(subset), config.get('qps', 2)) as lease:
                        attempt['queue'] = lease.record
                        attempt['status'] = 'grading'
                        checkpoint()
                        try:
                            command([cli, 'eval', 'grade', '--traces', trace, '--config', metrics,
                                     '--output', grades, '--qps', str(lease.qps)], folder / 'grade.log',
                                    config, records, config.get('grading_timeout', config.get('command_timeout', 1800)),
                                    pass_fds=(writer_fd, *lease.pass_fds), on_update=updated)
                        finally:
                            # Throttling in either SDK logs or a returned native error triggers a shared cooldown.
                            if observed_throttle(folder / 'grade.log', grades):
                                lease.throttled()
                except (Exception, KeyboardInterrupt) as exc:
                    interrupted = isinstance(exc, KeyboardInterrupt)
                    attempt['failure'] = f'{type(exc).__name__}: {exc}'
                    attempt['status'] = 'failed'
                    if not records:
                        attempt['command'] = {'exit_code':124, 'status':'queue_or_launch_failed'}
                finally:
                    if records:
                        attempt['command'] = records[-1]
                    results = list(grades.glob('results_*.json'))
                    if len(results) == 1:
                        attempt.update(results=str(results[0].relative_to(output)), results_sha256=digest(results[0]))
                    if attempt['command']['exit_code'] == 0:
                        attempt['status'] = 'completed' if len(results) == 1 else 'missing_result'
                    checkpoint()
                if interrupted:
                    raise KeyboardInterrupt('Grading interrupted; remaining jobs were not started')
                # Corruption or ambiguous partial output fails closed before any further job.
                resolve(output, suite, entry, policy['suites'][suite])
    identity(config, manifest)


def run(config, output):
    from dotenv import load_dotenv
    from .validation import validate
    load_dotenv(config['_root'] / '.env', override=False)
    plan = validate(config)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'schema_version':3, 'created_at':datetime.now(timezone.utc).isoformat(),
                'config_sha256':digest(config['_path']), 'policy_sha256':digest(config['policy']),
                'commands':[], 'suites':{}, 'versions':{}, 'errors':[], 'generation_complete':False,
                'source_sha256':source_hashes(config), 'agent':config['agent'], 'plan':plan}
    manifest['execution'] = {'mode':'hosted' if config.get('hosted') else 'local',
                             'case_concurrency':config.get('case_concurrency', 1)}
    if config.get('hosted'):
        manifest['execution'].update(endpoint=config['hosted']['url'], auth=config['hosted']['auth'],
            deployment_manifest_sha256=digest(config['hosted']['deployment_manifest']))
        shutil.copyfile(config['hosted']['deployment_manifest'], output / 'deployment.json')
    policy = read(config['policy'])
    shutil.copyfile(config['policy'], output / 'policy.json')
    checkpoint = lambda: save(output / 'manifest.json', manifest)
    with writer_lock(output) as fd:
        checkpoint()
        try:
            manifest['versions'] = versions()
            cli = Path(sys.executable).parent / 'agents-cli'
            command([sys.executable, '-m', 'adk_eval_harness', 'validate', '--config', config['_path']],
                    output / 'validate.log', config, manifest['commands'], config.get('command_timeout', 1800),
                    pass_fds=(fd,), on_update=checkpoint)
            command([cli, 'eval', 'metric', 'list'], output / 'metric-list.txt', config, manifest['commands'],
                    config.get('command_timeout', 1800), pass_fds=(fd,), on_update=checkpoint)
            # Capture every suite before any grading, enabling agent-free recovery.
            for suite, spec in config['suites'].items():
                out = output / suite
                out.mkdir()
                inputs = conversations(spec['dataset'])
                trace = out / 'traces.json'
                print(f'{suite}: generating live ADK traces', flush=True)
                command([sys.executable, '-m', 'adk_eval_harness', 'generate', '--config', config['_path'],
                         '--suite', suite, '--output', trace], out / 'generate.log', config, manifest['commands'],
                        config.get('generation_timeout', 60 + sum(len(c['messages']) for c in inputs) * config.get('turn_timeout', 180)),
                        pass_fds=(fd,), on_update=checkpoint)
                generated = read(trace)['eval_cases']
                expected = set(policy['suites'][suite]['cases'])
                if len(generated) != len(expected) or {c['eval_case_id'] for c in generated} != expected:
                    raise ValueError('Generated case coverage differs from policy')
                manifest['suites'][suite] = {'trace':str(trace.relative_to(output)),
                                             'trace_sha256':digest(trace), 'attempts':[]}
                checkpoint()
            identity(config, manifest)
            manifest['generation_complete'] = True
            checkpoint()
            grade_pending(config, output, manifest, policy, fd)
        except KeyboardInterrupt as exc:
            manifest['interruption'] = str(exc)
        except Exception as exc:
            manifest['errors'].append(f'{type(exc).__name__}: {exc}')
        return finish(output, manifest)


def recover(config, source, output):
    """Append recovery in a new run directory; never import or execute the configured agent."""
    from dotenv import load_dotenv
    from .attempts import resolve
    from .lineage import verify_lineage
    load_dotenv(config['_root'] / '.env', override=False)
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Recovery output must be a new independent directory')
    if output.exists():
        raise ValueError('Recovery output already exists')
    with writer_lock(source):
        parent = read(source / 'manifest.json')
        if (parent.get('schema_version') != 3 or parent.get('generation_complete') is not True
            or parent.get('errors') or not parent.get('commands')
            or any(c.get('exit_code') != 0 for c in parent['commands'])):
            raise ValueError('Recovery requires complete generation and an uncorrupted schema 3 run')
        identity(config, parent)
        if versions() != parent.get('versions'):
            raise ValueError('Recovery client versions differ from original run')
        verify_lineage(source, parent)
        from .integrity import verify_telemetry
        integrity, _ = verify_telemetry(source, parent)
        if integrity['errors']:
            raise ValueError('; '.join(integrity['errors']))
        policy = read(source / 'policy.json')
        if digest(source / 'policy.json') != parent['policy_sha256'] or set(parent['suites']) != set(policy['suites']):
            raise ValueError('Original policy or suite coverage differs')
        manifest = copy.deepcopy(parent)
        eligible = []
        for suite, entry in manifest['suites'].items():
            for attempt in entry['attempts']:
                command_record = attempt.get('command', {})
                if command_record.get('exit_code') is None:
                    command_record['status'] = 'interrupted'
            _, history, pending = resolve(source, suite, entry, policy['suites'][suite])
            counts = {}
            for item in history:
                for cid in item['case_ids']:
                    pair = (cid, item['metric'])
                    counts[pair] = counts.get(pair, 0) + 1
            eligible.extend((suite, *pair) for pair in pending
                            if counts.get(pair, 0) < config.get('max_grading_attempts', 3))
        if not eligible:
            raise ValueError('No recoverable grades remain within max_grading_attempts; valid scores cannot be retried')
        claim = source / 'recovery-link.json'
        if claim.exists():
            raise ValueError('This run already has a recovery successor; continue from that successor to preserve attempt limits')
        # Prepare the complete successor before claiming it. A copy failure must not
        # permanently strand the source's recoverable grades.
        shutil.copytree(source, output, ignore=shutil.ignore_patterns('.run.lock', 'recovery-link.json', '*.tmp'))
        history_dir = output / 'history'
        history_dir.mkdir(exist_ok=True)
        parent_name = f'history/{digest(source / "manifest.json")}.manifest.json'
        shutil.copyfile(source / 'manifest.json', output / parent_name)
        lineage = {'manifest':parent_name, 'manifest_sha256':digest(output / parent_name)}
        if (source / 'gate.json').is_file():
            gate_name = f'history/{digest(source / "gate.json")}.gate.json'
            shutil.copyfile(source / 'gate.json', output / gate_name)
            lineage.update(gate=gate_name, gate_sha256=digest(output / gate_name))
        manifest['parent'] = lineage
        manifest['created_at'] = datetime.now(timezone.utc).isoformat()
        manifest['recovery'] = {'eligible_pairs':len(eligible), 'agent_execution':False}
        manifest.pop('interruption', None)
        with writer_lock(output) as fd:
            save(output / 'manifest.json', manifest)
            # One successor per source, serialized by the source writer lock.
            with claim.open('x') as stream:
                json.dump({'output':str(output), 'created_at':datetime.now(timezone.utc).isoformat()}, stream)
            try:
                grade_pending(config, output, manifest, policy, fd)
            except KeyboardInterrupt as exc:
                manifest['interruption'] = str(exc)
            except Exception as exc:
                manifest['errors'].append(f'{type(exc).__name__}: {exc}')
            return finish(output, manifest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['run', 'generate', 'check', 'validate', 'recover', 'report'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--suite')
    parser.add_argument('--from-run', type=Path)
    args = parser.parse_args()
    if args.action == 'report':
        if args.output is None:
            parser.error('--output is required for report')
        from .reporting import write_report
        write_report(args.output.resolve())
        print(str(args.output.resolve() / 'report.html'))
        return 0
    if args.config is None:
        parser.error('--config is required for this action')
    config = load(args.config)
    if args.action == 'validate':
        from .validation import validate
        plan = validate(config)
        metadata = inspect_agent(config)
        print(json.dumps({**plan, 'agent_metadata_compatible':True, 'agent_names':list(metadata)}, indent=2))
        return 0
    if args.output is None:
        parser.error('--output is required for run, generate, and check')
    output = args.output.resolve()
    if args.action == 'recover':
        if args.from_run is None:
            parser.error('--from-run is required for recover')
        return recover(config, args.from_run, output)
    if args.action == 'generate':
        generate(config, args.suite, output)
        return 0
    if args.action == 'check':
        manifest = read(output / 'manifest.json')
        if (manifest.get('config_sha256') != digest(config['_path'])
            or manifest.get('policy_sha256') != digest(config['policy'])
            or manifest.get('agent') != config['agent']
            or manifest.get('source_sha256') != source_hashes(config)):
            print(json.dumps({'passed':False, 'evaluation_complete':False,
                              'errors':['Current config, policy, agent or sources do not match evaluated evidence']}))
            return 1
        report = evaluate(output, output / 'policy.json')
        print(json.dumps(report, indent=2))
        return 0 if report['passed'] else 1
    return run(config, output)
