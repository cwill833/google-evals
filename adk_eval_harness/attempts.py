"""Immutable native grading attempts; retry only unavailable grades, never low scores."""
import copy
import math
from pathlib import Path
import re

import yaml

from .gate import comparable, digest, read, json_equal


def require(condition, message):
    if not condition:
        raise ValueError(message)


def artifact(run, entry, name):
    """Bind a ledger reference to immutable bytes within this run."""
    relative = entry.get(name)
    require(isinstance(relative, str) and bool(relative), f'missing {name} path')
    path = (run / relative).resolve()
    require(path.is_relative_to(run), f'{name} path escapes run')
    require(path.is_file(), f'missing {name} artifact')
    require(entry.get(name + '_sha256') == digest(path), f'{name} artifact hash mismatch')
    return path


def case_list(data, label):
    require(isinstance(data, dict) and isinstance(data.get('eval_cases'), list), f'{label}: invalid eval_cases')
    cases = data['eval_cases']
    ids = [c.get('eval_case_id') if isinstance(c, dict) else None for c in cases]
    require(bool(cases) and all(isinstance(cid, str) and cid for cid in ids)
            and len(ids) == len(set(ids)), f'{label}: empty, invalid or duplicate case IDs')
    return cases, ids


def native_name(name, requested, versions):
    require(isinstance(name, str), 'missing native metric name')
    explicit = re.search(r'_v[0-9]+$', requested)
    require(name == requested or (not explicit and re.fullmatch(re.escape(requested) + r'_v[0-9]+', name)),
            f'native metric {name!r} does not match requested {requested!r}')
    previous = versions.setdefault(requested, name)
    require(name == previous, f'native metric version changed across attempts: {previous} -> {name}')


def metric_status(value, name):
    """Unavailable is retryable; malformed data is corruption, not a low-quality score."""
    require(isinstance(value, dict) and value.get('metric_name') == name, 'malformed native metric payload')
    allowed = {'metric_name', 'score', 'error_message', 'explanation', 'rubric_verdicts', 'raw_output'}
    require(not set(value) - allowed, 'unknown native metric payload fields')
    error = value.get('error_message')
    require(error is None or isinstance(error, str), 'malformed native error_message')
    score = value.get('score')
    if score is not None:
        require(type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1,
                'malformed native score')
    if error:
        return error
    return 'Native metric score unavailable' if score is None else None


def resolve(run, suite, entry, requirements):
    """Return derived effective result, transparent history, and pending (case, metric) pairs.

    No file is written. A successful finite native score is sticky even when zero.
    Existing corrupt artifacts or retrying any completed pair always raises ValueError.
    """
    run = Path(run).resolve()
    try:
        original, ids = case_list(read(artifact(run, entry, 'trace')), 'original trace')
        require(set(ids) == set(requirements['cases']), 'original case coverage mismatch')
        requested_metrics = requirements['metrics']
        require(isinstance(requested_metrics, list) and bool(requested_metrics)
                and all(isinstance(m, str) and m for m in requested_metrics)
                and len(requested_metrics) == len(set(requested_metrics)), 'invalid required metrics')
        require(len({re.sub(r'_v[0-9]+$', '', m) for m in requested_metrics}) == len(requested_metrics),
                'ambiguous required metric aliases')
        original_by_id = dict(zip(ids, original))
        selected, versions, attempted = {}, {}, set()
        history = []
        attempts = entry.get('attempts')
        require(isinstance(attempts, list), 'attempts must be a list')
        for index, attempt in enumerate(attempts):
            require(isinstance(attempt, dict), 'attempt must be an object')
            requested = attempt.get('metric')
            require(requested in requested_metrics, 'attempt contains unrequested metric')
            trace_path = artifact(run, attempt, 'trace')
            cases, subset_ids = case_list(read(trace_path), f'attempt {index} trace')
            for case, cid in zip(cases, subset_ids):
                require(cid in original_by_id and json_equal(case, original_by_id[cid]),
                        f'attempt {index}: case {cid} differs from original trace')
                require((cid, requested) not in selected,
                        f'attempt {index}: retry of completed pair {cid}/{requested}')
            metric_path = artifact(run, attempt, 'metric_config')
            metric_config = yaml.safe_load(metric_path.read_text())
            require(isinstance(metric_config, dict) and set(metric_config) == {'metrics_to_run'}
                    and metric_config['metrics_to_run'] == [requested],
                    f'attempt {index}: metric config must select only {requested}')
            command = attempt.get('command')
            interrupted = (isinstance(command, dict) and command.get('exit_code') is None
                           and command.get('status') == 'interrupted' and not attempt.get('results'))
            require(isinstance(command, dict) and (type(command.get('exit_code')) is int or interrupted),
                    f'attempt {index}: finished exit code or interrupted-without-result status required')
            argv = command.get('argv')
            if argv is not None:
                require(isinstance(argv, list) and all(isinstance(a, str) for a in argv)
                        and len(argv) >= 3 and argv[1:3] == ['eval', 'grade'],
                        f'attempt {index}: not a native grade command')
                for flag in ['--traces', '--config']:
                    require(argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv),
                            f'attempt {index}: missing or duplicate {flag}')
                # argv is historical audit data: absolute paths may differ after export.
                # Hash-bound trace/config artifacts establish the replayed inputs.
            if 'grades_dir' in attempt:
                require(isinstance(attempt['grades_dir'], str), 'invalid grades_dir')
                grades_dir = (run / attempt['grades_dir']).resolve()
                require(grades_dir.is_relative_to(run), 'grades_dir path escapes run')
                found = list(grades_dir.glob('results_*.json'))
                declared = (run / attempt['results']).resolve() if attempt.get('results') else None
                require(not found or (len(found) == 1 and found[0].resolve() == declared),
                        'undeclared or ambiguous native results; cannot recover this attempt')
            item = {'attempt_index': index, 'metric': requested, 'case_ids': subset_ids,
                    'trace': attempt['trace'], 'metric_config': attempt['metric_config'],
                    'results': attempt.get('results'), 'exit_code': command['exit_code'],
                    'errors': [], 'selected_pairs': [], 'recovered_pairs': []}
            valid = {}
            unavailable = {cid: 'Native result file unavailable' for cid in subset_ids}
            if not attempt.get('results'):
                require(command.get('exit_code') != 0 or attempt.get('status') == 'missing_result',
                        f'attempt {index}: successful command without result artifact or explicit missing_result status')
                require('results_sha256' not in attempt, 'results hash without results artifact')
            else:
                result = read(artifact(run, attempt, 'results'))
                require(isinstance(result, dict) and isinstance(result.get('evaluation_dataset'), list),
                        f'attempt {index}: malformed evaluation_dataset')
                returned_cases = []
                for dataset in result['evaluation_dataset']:
                    returned_cases.extend(case_list(dataset, 'native evaluation_dataset')[0])
                require(json_equal([comparable(c) for c in returned_cases], [comparable(c) for c in cases]),
                        f'attempt {index}: native dataset differs from attempt trace')
                rows = result.get('eval_case_results')
                require(isinstance(rows, list), f'attempt {index}: malformed result rows')
                seen = set()
                for row in rows:
                    require(isinstance(row, dict), 'malformed native result row')
                    position = row.get('eval_case_index')
                    require(type(position) is int and 0 <= position < len(cases) and position not in seen,
                            f'attempt {index}: invalid or duplicate case index')
                    seen.add(position)
                    cid = subset_ids[position]
                    candidates = row.get('response_candidate_results')
                    require(isinstance(candidates, list) and len(candidates) <= 1,
                            'malformed native response candidates')
                    if not candidates:
                        unavailable[cid] = 'Native response candidate unavailable'
                        continue
                    candidate = candidates[0]
                    require(isinstance(candidate, dict) and type(candidate.get('response_index')) is int
                            and candidate['response_index'] == 0, 'invalid native response index')
                    metrics = candidate.get('metric_results')
                    require(isinstance(metrics, dict) and len(metrics) <= 1, 'unknown or malformed native metric coverage')
                    if not metrics:
                        unavailable[cid] = 'Native metric unavailable'
                        continue
                    name, payload = next(iter(metrics.items()))
                    native_name(name, requested, versions)
                    failure = metric_status(payload, name)
                    if failure is None:
                        valid[cid] = payload
                        unavailable.pop(cid)
                    else:
                        unavailable[cid] = failure
                summaries = result.get('summary_metrics')
                require(isinstance(summaries, list) and len(summaries) == 1
                        and isinstance(summaries[0], dict), 'native summary coverage mismatch')
                summary = summaries[0]
                native_name(summary.get('metric_name'), requested, versions)
                expected_counts = {'num_cases_total':len(cases), 'num_cases_valid':len(valid),
                                   'num_cases_error':len(cases) - len(valid)}
                require(all(type(summary.get(k)) is int and summary[k] == v for k, v in expected_counts.items()),
                        'native summary counts disagree with valid/error/missing rows')
                require(command['exit_code'] == 0 or not valid,
                        f'attempt {index}: nonzero command returned valid scores; ambiguous execution')
            for cid, payload in valid.items():
                key = (cid, requested)
                selected[key] = copy.deepcopy(payload)
                item['selected_pairs'].append([cid, requested])
                if key in attempted:
                    item['recovered_pairs'].append([cid, requested])
            for cid, message in unavailable.items():
                item['errors'].append({'case':cid, 'metric':requested, 'message':message})
            attempted.update((cid, requested) for cid in subset_ids)
            history.append(item)
        pending = [(cid, metric) for cid in ids for metric in requested_metrics if (cid, metric) not in selected]
        rows = []
        for index, cid in enumerate(ids):
            metrics = {}
            for requested in requested_metrics:
                native = versions.get(requested, requested)
                metrics[native] = selected.get((cid, requested), {
                    'metric_name': native, 'score': None, 'error_message': 'No valid native grade in attempt ledger'})
            rows.append({'eval_case_index':index, 'response_candidate_results':[
                {'response_index':0, 'metric_results':metrics}]})
        summaries = []
        for metric in requested_metrics:
            count = sum((cid, metric) in selected for cid in ids)
            summaries.append({'metric_name':versions.get(metric, metric), 'num_cases_total':len(ids),
                              'num_cases_valid':count, 'num_cases_error':len(ids) - count})
        for item in history:
            for error in item['errors']:
                error['recovered'] = (error['case'], error['metric']) in selected
        effective = {'evaluation_dataset':[{'eval_cases':original}], 'eval_case_results':rows,
                     'summary_metrics':summaries,
                     'metadata':{'provenance':'derived_attempt_ledger_view',
                                 'summary_origin':'derived counts; not a native service summary',
                                 'selection':'first valid native grade; never maximum score'}}
        return effective, history, pending
    except (KeyError, TypeError, IndexError, OSError, yaml.YAMLError) as exc:
        raise ValueError(f'{suite}: malformed grading attempt ledger: {exc}') from exc
