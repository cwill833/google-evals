"""Readable, offline reports over recorded evidence; never a replacement grader."""
import html
import json
import math
import re
from pathlib import Path
from urllib.parse import quote

from .gate import digest, read, requested_metric_name, evaluate

TOKEN_FIELDS = ('prompt_token_count', 'candidates_token_count', 'thoughts_token_count',
                'cached_content_token_count', 'total_token_count')


def content_text(content):
    return ''.join(part.get('text', '') for part in (content or {}).get('parts', [])
                   if isinstance(part.get('text'), str) and not part.get('thought'))


def usage_summary(events):
    """Sum observed nonpartial usage once per event/invocation; never infer missing tokens."""
    counts = {name: None for name in TOKEN_FIELDS}
    coverage = {name: 0 for name in TOKEN_FIELDS}
    seen, measured, partial, duplicates, uncertain = {}, 0, 0, 0, []
    for index, event in enumerate(events):
        usage = event.get('usage_metadata')
        if not isinstance(usage, dict) or not usage:
            continue
        if event.get('partial') is True:
            partial += 1
            continue
        event_id, invocation_id = event.get('id'), event.get('invocation_id')
        key = (event_id, invocation_id) if event_id and invocation_id else ('unidentified', index)
        if not event_id or not invocation_id:
            uncertain.append('Some usage events lack an event ID or invocation ID; deduplication is incomplete.')
        if key in seen:
            duplicates += 1
            if seen[key] != usage:
                uncertain.append('Repeated event identity has conflicting usage; only its first record was counted.')
            continue
        seen[key] = usage
        measured += 1
        for field in TOKEN_FIELDS:
            value = usage.get(field)
            if type(value) is int and value >= 0:
                counts[field] = (counts[field] or 0) + value
                coverage[field] += 1
            elif value is not None:
                uncertain.append('Malformed token values were excluded.')
    if partial:
        uncertain.append('Partial streaming usage was excluded to avoid double counting; recorded totals may be incomplete.')
    return {'status': 'unavailable' if not measured else ('partial_or_uncertain' if uncertain else 'reported'),
            'observed_token_counts': counts, 'field_event_coverage': coverage,
            'measured_events': measured, 'excluded_partial_events': partial,
            'duplicate_events_excluded': duplicates, 'notes': list(dict.fromkeys(uncertain)),
            'interpretation': 'Observed agent usage, not a billing total. Token categories can overlap; do not add them to total_token_count.'}


def latency_summary(turns):
    measured = [turn['telemetry']['duration_seconds'] for turn in turns
                if turn.get('telemetry', {}).get('duration_seconds') is not None]
    ordered = sorted(measured)
    count = len(ordered)
    return {'status': 'unavailable' if not measured else ('reported' if count == len(turns) else 'partial'),
            'recorded_turns': count, 'total_turns': len(turns),
            'observed_duration_seconds': sum(ordered) if ordered else None,
            'mean_turn_seconds': sum(ordered) / count if ordered else None,
            'p50_turn_seconds': ordered[math.ceil(.50 * count) - 1] if ordered else None,
            'p95_turn_seconds': ordered[math.ceil(.95 * count) - 1] if ordered else None,
            'interpretation': 'Client-measured turn execution and state snapshot time. Percentiles use nearest rank; no event-timestamp inference.'}


def _telemetry(raw):
    value = raw.get('telemetry') or {}
    result = {'status': value.get('status', 'unavailable'), 'clock': value.get('clock'),
              'scope': value.get('scope'), 'error_class': value.get('error_class')}
    for field in ('duration_seconds', 'time_to_first_event_seconds', 'time_to_first_text_seconds'):
        number = value.get(field)
        result[field] = number if type(number) in (int, float) and math.isfinite(number) and number >= 0 else None
    return result


def _path(run, relative):
    if not isinstance(relative, str):
        raise ValueError('Artifact reference must be a relative path')
    path = (run / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(run):
        raise ValueError('Report artifact escapes run directory')
    if not path.is_file():
        raise ValueError('Report artifact is missing: ' + relative)
    return path


def _link(run, path, label):
    relative = str(path.resolve().relative_to(run))
    return {'label': label, 'path': relative, 'sha256': digest(path)}


def _checked(run, entry, key):
    path = _path(run, entry[key])
    if digest(path) != entry[key + '_sha256']:
        raise ValueError('Report artifact hash mismatch: ' + entry[key])
    return path


def _events(turn):
    messages = []
    for event in turn.get('events', []):
        parts = (event.get('content') or {}).get('parts') or []
        message = {'author': event.get('author'), 'role': (event.get('content') or {}).get('role'),
                   'text': content_text(event.get('content')), 'tool_calls': [], 'tool_responses': []}
        for part in parts:
            if part.get('function_call'):
                message['tool_calls'].append(part['function_call'])
            if part.get('function_response'):
                message['tool_responses'].append(part['function_response'])
        if message['text'] or message['tool_calls'] or message['tool_responses']:
            messages.append(message)
    return messages


def _partial_case(run, suite, cid, session_artifact):
    directory = (run / suite / 'sessions' / cid).resolve()
    if not directory.is_relative_to(run):
        raise ValueError('Partial session path escapes run')
    if not directory.is_dir():
        return None, []
    files = sorted((path for path in directory.iterdir() if re.fullmatch(r'turn_[0-9]+\.json', path.name)),
                   key=lambda path: int(path.stem.split('_')[1]))
    error_path = directory / 'case_error.json'
    if not files and not error_path.is_file():
        return None, []
    case = {'suite': suite, 'case_id': cid, 'evidence_status': 'partial_ungraded',
            'verdict': 'incomplete', 'prompt': '', 'final_response': '', 'reference': '',
            'errors': ['Raw partial execution only; no verified complete trace or native grades.'],
            'warnings': [], 'turns': [], 'native_grades': [], 'attempts': [], 'artifacts': []}
    if error_path.is_file():
        verified_error = session_artifact(error_path, case, 'Case execution failure')
        if verified_error:
            failure = read(verified_error)
            case['errors'].append('Case execution ' + str(failure.get('status')) + ': ' +
                                  str(failure.get('error_class')) + '; no agent retry attempted.')
    cleanup_path = directory / 'cleanup_error.json'
    if cleanup_path.is_file():
        verified_cleanup = session_artifact(cleanup_path, case, 'Runner cleanup failure')
        if verified_cleanup:
            cleanup = read(verified_cleanup)
            case['errors'].append('Runner cleanup failed: ' + str(cleanup.get('error_class')))
    events = []
    for path in files:
        path = session_artifact(path, case, 'Partial raw turn')
        if path is None:
            continue
        raw = read(path)
        raw_events = raw.get('events') or []
        user = raw.get('user_message')
        display_events = ([{'author': 'user', 'content': user}] if user else []) + raw_events
        turn = {'turn_index': int(path.stem.split('_')[1]), 'events': _events({'events': display_events}),
                'telemetry': _telemetry(raw), 'agent_usage': usage_summary(raw_events)}
        case['turns'].append(turn)
        events.extend(raw_events)
        if not case['prompt']:
            case['prompt'] = content_text(raw.get('user_message'))
    for path in directory.iterdir():
        if path.name == 'runner_metadata.json' or re.fullmatch(r'state_[0-9]+\.json', path.name):
            session_artifact(path, case, 'Session state / runner metadata')
    case['agent_usage'] = usage_summary(events)
    case['agent_latency'] = latency_summary(case['turns'])
    return case, events


def build_report(run):
    run = Path(run).resolve()
    manifest, policy = read(run / 'manifest.json'), read(run / 'policy.json')
    recorded_gate = read(run / 'gate.json') if (run / 'gate.json').is_file() else None
    # Recheck hashes, lineage, native coverage and trusted deterministic hooks.
    # This performs no agent execution and no hosted grading calls.
    gate = evaluate(run, run / 'policy.json')
    report = {'schema_version': 1, 'title': 'ADK evaluation report',
              'provenance': 'Offline replay of native evidence integrity and deterministic policy using currently trusted hooks; no new agent execution or hosted grading.',
              'recorded_gate': recorded_gate, 'replayed_gate': gate,
              'execution': manifest.get('execution', {'mode': 'unavailable'}),
              'passed': gate.get('passed') is True, 'evaluation_complete': gate.get('evaluation_complete') is True,
              'created_at': manifest.get('created_at'), 'agent': manifest.get('agent'),
              'errors': list(gate.get('errors', [])), 'warnings': list(gate.get('warnings', [])),
              'recovered_grading_failures': gate.get('recovered_grading_failures', 0),
              'cases': [], 'artifacts': [],
              'judge_usage': {'status': 'unavailable', 'token_counts': None,
                              'note': 'Hosted native judge token usage is not exposed by these recorded native results; agent tokens below are separate.'}}
    if recorded_gate is not None and (recorded_gate.get('passed') != gate.get('passed') or recorded_gate.get('evaluation_complete') != gate.get('evaluation_complete')):
        report['warnings'].append('Recorded gate verdict differs from current offline replay; this report uses the replayed verdict.')
    for name in ('manifest.json', 'policy.json', 'gate.json'):
        if (run / name).is_file():
            report['artifacts'].append(_link(run, _path(run, name), name))
    hashes = manifest.get('telemetry_sha256')
    from .integrity import verify_telemetry
    integrity, checked = verify_telemetry(run, manifest)
    report['telemetry_integrity'] = integrity
    def telemetry_error(message):
        if message not in report['telemetry_integrity']['errors']:
            report['telemetry_integrity']['errors'].append(message)
        if message not in report['errors']:
            report['errors'].append(message)
        report['passed'], report['evaluation_complete'] = False, False
    for message in list(integrity['errors']):
        telemetry_error(message)
    def session_artifact(path, case, label):
        relative = str(path.relative_to(run))
        try:
            if 'telemetry_sha256' not in manifest:
                safe = _path(run, relative)
            else:
                safe = checked.get(relative)
                if safe is None:
                    raise ValueError('missing or mismatched manifest hash')
            case['artifacts'].append(_link(run, safe, label))
            return safe
        except (ValueError, OSError) as exc:
            message = f'Telemetry excluded for {relative}: {exc}'
            telemetry_error(message)
            case['warnings'].append(message)
            return None
    def observed_sum(values):
        numbers = [value for value in values if type(value) in (int, float) and math.isfinite(value) and value >= 0]
        return {'observed_seconds': sum(numbers) if numbers else None, 'recorded_observations': len(numbers)}
    attempts = [attempt for entry in manifest.get('suites', {}).values() for attempt in entry.get('attempts', [])]
    commands = manifest.get('commands', [])
    legacy_grading = [command for command in commands if command.get('argv', [])[1:3] == ['eval', 'grade']]
    agent_commands = [command for command in commands if command not in legacy_grading]
    report['runtime_summary'] = {
        'generation_and_preflight_commands': observed_sum([command.get('elapsed_seconds') for command in agent_commands]),
        'native_grading_commands': observed_sum([(attempt.get('command') or {}).get('elapsed_seconds') for attempt in attempts] + [command.get('elapsed_seconds') for command in legacy_grading]),
        'shared_queue_wait': observed_sum([(attempt.get('queue') or {}).get('wait_seconds') for attempt in attempts]),
        'interpretation': 'Recorded command durations and queue waits, summed separately. These are not an inferred end-to-end runtime.'}
    all_events, all_turns, judge_usage = [], [], []
    for suite, entry in manifest.get('suites', {}).items():
        try:
            trace_path = _checked(run, entry, 'trace')
            traces = read(trace_path)['eval_cases']
            if manifest.get('schema_version') == 3:
                from .attempts import resolve
                result, history, _ = resolve(run, suite, entry, policy['suites'][suite])
                result_paths = [_checked(run, attempt, 'results') for attempt in entry['attempts'] if attempt.get('results')]
            else:
                result_paths = [_checked(run, entry, 'results')]
                result, history = read(result_paths[0]), []
            # Only explicitly exposed judge usage can be displayed; never infer it from agent traces.
            for path in result_paths:
                native = read(path)
                metadata = native.get('metadata') or {}
                if isinstance(metadata, dict) and isinstance(metadata.get('judge_usage_metadata'), dict):
                    judge_usage.append({'artifact': str(path.relative_to(run)), 'reported': metadata['judge_usage_metadata']})
            by_index = {row['eval_case_index']: row for row in result['eval_case_results']}
            for index, case in enumerate(traces):
                cid = case['eval_case_id']
                scoped = lambda items: [item for item in items if item.startswith(f'{suite}/{cid}:') or item.startswith(f'{suite}/{cid}/')]
                item = {'suite': suite, 'case_id': cid, 'evidence_status': 'native_trace', 'prompt': content_text(case.get('prompt')),
                        'final_response': content_text((case.get('responses') or [{}])[0].get('response')),
                        'reference': content_text((case.get('reference') or {}).get('response')),
                        'errors': scoped(report['errors']), 'warnings': scoped(report['warnings']),
                        'turns': [], 'native_grades': [], 'attempts': [],
                        'artifacts': [_link(run, trace_path, 'Original trace')]}
                raw_case_events = []
                for position, turn in enumerate(case.get('agent_data', {}).get('turns', [])):
                    turn_item = {'turn_index': turn.get('turn_index', position), 'events': _events(turn)}
                    raw_path = trace_path.parent / 'sessions' / cid / f'turn_{position}.json'
                    raw = {}
                    if raw_path.is_file():
                        raw_path = session_artifact(raw_path, item, f'Turn {position + 1} raw events')
                        if raw_path is not None:
                            raw = read(raw_path)
                    raw_events = raw.get('events') or []
                    raw_case_events.extend(raw_events)
                    turn_item.update(telemetry=_telemetry(raw), agent_usage=usage_summary(raw_events))
                    item['turns'].append(turn_item)
                session_dir = trace_path.parent / 'sessions' / cid
                if session_dir.is_dir():
                    for path in session_dir.iterdir():
                        if path.name == 'runner_metadata.json' or re.fullmatch(r'state_[0-9]+\.json', path.name):
                            session_artifact(path, item, 'Session state / runner metadata')
                candidates = by_index.get(index, {}).get('response_candidate_results') or []
                metrics = candidates[0].get('metric_results', {}) if candidates else {}
                for native_name, payload in metrics.items():
                    requested = requested_metric_name(native_name, policy['suites'][suite]['metrics'])
                    item['native_grades'].append({'metric': requested, 'native_metric_name': native_name,
                        'threshold': policy['thresholds'][requested],
                        'role': policy.get('metric_roles', {}).get(requested, 'blocking'), 'payload': payload})
                for attempt in history:
                    if cid in attempt['case_ids']:
                        item['attempts'].append({**attempt, 'case_ids': [cid], 'batch_case_count': len(attempt['case_ids']),
                            'errors': [error for error in attempt['errors'] if error['case'] == cid],
                            'selected_pairs': [pair for pair in attempt['selected_pairs'] if pair[0] == cid],
                            'recovered_pairs': [pair for pair in attempt['recovered_pairs'] if pair[0] == cid]})
                case_results = {attempt.get('results') for attempt in history if cid in attempt['case_ids']}
                for path in result_paths:
                    if history and str(path.relative_to(run)) not in case_results:
                        continue
                    item['artifacts'].append(_link(run, path, 'Native grading result'))
                item['agent_usage'] = usage_summary(raw_case_events)
                item['agent_latency'] = latency_summary(item['turns'])
                incomplete = not item['native_grades'] or any(grade['payload'].get('score') is None or grade['payload'].get('error_message')
                                                           for grade in item['native_grades'])
                replayed_case = any(score.get('suite') == suite and score.get('case') == cid for score in gate.get('scores', []))
                item['verdict'] = 'incomplete' if incomplete else ('unverified' if not replayed_case else ('failed' if item['errors'] else 'passed'))
                report['cases'].append(item)
                all_events.extend(raw_case_events)
                all_turns.extend(item['turns'])
        except (ValueError, KeyError, TypeError, IndexError, OSError) as exc:
            report['errors'].append(f'Report evidence unavailable for {suite}: {type(exc).__name__}: {exc}')
            report['passed'], report['evaluation_complete'] = False, False
    # A failed generation may never have produced a finalized trace. Surface only
    # known policy cases from safe raw-session paths, explicitly without grades.
    seen_cases = {(case['suite'], case['case_id']) for case in report['cases']}
    for suite, requirements in policy.get('suites', {}).items():
        if not isinstance(suite, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', suite):
            continue
        for cid in requirements.get('cases', {}):
            if (suite, cid) in seen_cases or not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', cid):
                continue
            try:
                partial, raw_events = _partial_case(run, suite, cid, session_artifact)
                if partial:
                    report['cases'].append(partial)
                    all_events.extend(raw_events)
                    all_turns.extend(partial['turns'])
                    report['passed'], report['evaluation_complete'] = False, False
            except (ValueError, KeyError, TypeError, OSError) as exc:
                report['warnings'].append(f'Partial evidence unavailable for {suite}/{cid}: {type(exc).__name__}: {exc}')
    if judge_usage:
        report['judge_usage'] = {'status': 'reported', 'observations': judge_usage,
                                 'note': 'Explicit native judge metadata, shown separately without inferred totals.'}
    report['agent_usage'] = usage_summary(all_events)
    report['agent_latency'] = latency_summary(all_turns)
    report['case_count'] = len(report['cases'])
    report['partial_case_count'] = sum(case['evidence_status'] == 'partial_ungraded' for case in report['cases'])
    report['requested_grade_count'] = sum(len(suite.get('cases', {})) * len(suite.get('metrics', []))
                                          for suite in policy.get('suites', {}).values())
    report['native_grade_count'] = sum(
        type(grade['payload'].get('score')) in (int, float)
        and math.isfinite(grade['payload']['score']) and 0 <= grade['payload']['score'] <= 1
        and not grade['payload'].get('error_message')
        for case in report['cases'] for grade in case['native_grades'])
    report['unavailable_grade_count'] = max(0, report['requested_grade_count'] - report['native_grade_count'])
    return report


def _escape(value):
    return html.escape(str(value), quote=True)


def _pretty(value):
    return '<pre>' + _escape(json.dumps(value, indent=2, ensure_ascii=False)) + '</pre>'


def _links(artifacts):
    return '<ul>' + ''.join('<li><a href="' + _escape(quote(artifact['path'], safe='/')) + '">' +
                           _escape(artifact['label']) + '</a></li>' for artifact in artifacts) + '</ul>'


def _number(value, digits=3):
    return 'Unavailable' if type(value) not in (int, float) or not math.isfinite(value) else f'{value:.{digits}f}'


def render_html(report):
    verdict = 'PASS' if report['passed'] else ('INCOMPLETE' if not report['evaluation_complete'] else 'FAIL')
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">',
             '<title>ADK evaluation report</title><style>',
             'body{font:16px/1.5 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#17212b;background:#fff}',
             'h1,h2,h3{line-height:1.2}table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid #bbc5cf;padding:.5rem;text-align:left;vertical-align:top}',
             'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f7;padding:.8rem}details{border:1px solid #ccd4dc;padding:.7rem;margin:.8rem 0}',
             'summary{cursor:pointer;font-weight:600}a{color:#0756a0}.status{font-weight:700}.note{color:#455565}section{margin:2rem 0}caption{text-align:left;font-weight:600}',
             '</style></head><body><h1>ADK evaluation report</h1>',
             f'<p class="status">{verdict} · {_escape(report["case_count"])} cases · {_escape(report["native_grade_count"])} / {_escape(report["requested_grade_count"])} completed native grades</p>',
             '<p>' + _escape(report.get('agent')) + '</p><p class="note">' + _escape(report['provenance']) + '</p>',
             '<p>Recovered grading failures: ' + _escape(report['recovered_grading_failures']) + '.</p>',
             '<h2>Execution target</h2>', _pretty(report['execution']),
             '<details><summary>Command duration and queue wait</summary>', _pretty(report['runtime_summary']), '</details>']
    for label in ('errors', 'warnings'):
        if report[label]:
            parts.append('<h2>' + label.title() + '</h2><ul>' + ''.join('<li>' + _escape(value) + '</li>' for value in report[label]) + '</ul>')
    parts += ['<h2>Agent telemetry</h2><p>Missing values mean unavailable, never zero. These are agent observations, separate from hosted judge usage.</p>',
              _pretty(report['telemetry_integrity']),
              '<table><caption>Observed agent tokens</caption><tr><th scope="col">Category</th><th scope="col">Tokens</th><th scope="col">Events reporting field</th></tr>']
    for field, value in report['agent_usage']['observed_token_counts'].items():
        parts.append(f'<tr><th scope="row">{_escape(field)}</th><td>{"Unavailable" if value is None else _escape(value)}</td><td>{report["agent_usage"]["field_event_coverage"][field]}</td></tr>')
    parts += ['</table><p class="note">' + _escape(report['agent_usage']['interpretation']) + '</p>',
              _pretty({key: value for key, value in report['agent_usage'].items() if key not in ('observed_token_counts', 'field_event_coverage', 'interpretation')}),
              '<h3>Turn latency</h3>', _pretty(report['agent_latency']), '<h3>Hosted judge usage</h3>', _pretty(report['judge_usage']),
              '<h2>Cases</h2><table><tr><th scope="col">Suite / case</th><th scope="col">Verdict</th><th scope="col">Observed turn time</th></tr>']
    for index, case in enumerate(report['cases']):
        parts.append(f'<tr><td><a href="#case-{index}">{_escape(case["suite"] + "/" + case["case_id"])}</a></td><td>{_escape(case["verdict"])}</td><td>{_number(case["agent_latency"]["observed_duration_seconds"])} seconds</td></tr>')
    parts.append('</table>')
    if report.get('partial_case_count'):
        parts.append('<p class=\"note\">' + _escape(report['partial_case_count']) + ' cases below contain partial raw execution only. They are ungraded and incomplete.</p>')
    for index, case in enumerate(report['cases']):
        parts.append(f'<section id="case-{index}"><h2>{_escape(case["suite"] + "/" + case["case_id"])}</h2><p class="status">{_escape(case["verdict"]).upper()}</p>')
        if case.get('evidence_status') == 'partial_ungraded':
            parts.append('<p class=\"note\">Partial raw execution: user prompts and emitted events are diagnostic evidence. No native grades or completed response are inferred.</p>')
        if case['prompt']:
            parts += ['<h3>Prompt</h3><pre>' + _escape(case['prompt']) + '</pre>']
        for turn in case['turns']:
            parts.append('<details><summary>Turn ' + _escape(turn['turn_index'] + 1) + ' · ' + _number(turn['telemetry']['duration_seconds']) + ' seconds</summary>')
            for event in turn['events']:
                parts.append('<h3>' + _escape(event.get('author') or event.get('role') or 'Event') + '</h3>')
                if event['text']:
                    parts.append('<pre>' + _escape(event['text']) + '</pre>')
                for kind, label in [('tool_calls', 'Tool calls'), ('tool_responses', 'Tool responses')]:
                    if event[kind]:
                        parts += ['<h4>' + label + '</h4>', _pretty(event[kind])]
            parts += ['<h3>Turn telemetry</h3>', _pretty(turn['telemetry']), _pretty(turn['agent_usage']), '</details>']
        if case['evidence_status'] != 'partial_ungraded':
            parts += ['<h3>Final response</h3><pre>' + _escape(case['final_response']) + '</pre>']
        if case['reference']:
            parts += ['<details><summary>Expected reference</summary><pre>' + _escape(case['reference']) + '</pre></details>']
        for grade in case['native_grades']:
            payload = grade['payload']
            parts.append('<details><summary>' + _escape(grade['native_metric_name']) + ' · score ' + _number(payload.get('score')) + ' · ' + _escape(grade['role']) + ' · threshold ' + _number(grade['threshold']) + '</summary>')
            parts += [_pretty(payload), '</details>']
        for label in ('errors', 'warnings'):
            if case[label]:
                parts += ['<h3>' + label.title() + '</h3>', _pretty(case[label])]
        if case['attempts']:
            parts += ['<details><summary>Grading attempts and recovery history</summary>', _pretty(case['attempts']), '</details>']
        parts += ['<details><summary>Raw case artifacts</summary>', _links(case['artifacts']), '</details></section>']
    parts += ['<h2>Run artifacts</h2>', _links(report['artifacts']), '</body></html>']
    return ''.join(parts)


def write_report(run):
    """Write report.json and a self-contained report.html; return the report dictionary."""
    run = Path(run).resolve()
    report = build_report(run)
    for name, value in [('report.json', json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n'),
                        ('report.html', render_html(report))]:
        path = run / name
        temporary = path.with_name(name + '.tmp')
        temporary.write_text(value)
        temporary.replace(path)
    return report
