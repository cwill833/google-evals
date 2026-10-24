"""Live ADK conversations in isolated, persistent sessions; native Google trace schema."""
import copy
import asyncio
import json
from pathlib import Path
import sys
import time
import re
import uuid
import math
from datetime import datetime, timezone

from google.adk.runners import InMemoryRunner
from google.genai import types as genai
from vertexai._genai import types


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def agent_metadata(agent):
    """Validate native export compatibility before executing any case."""
    from google.adk.agents import BaseAgent
    if not isinstance(agent, BaseAgent):
        raise TypeError('Configured agent must be an ADK BaseAgent')
    try:
        return types.evals.AgentData.get_agents_map(agent)
    except Exception as exc:
        raise ValueError('Agent metadata is unsupported by the pinned Google exporter; '
                         'check callable instructions and dynamic toolsets') from exc


async def capture_turn(runner, *, output, case_id, index, session, user_id, app_name, content, turn_timeout):
    """Capture client-observed timings and original events, including failed turns."""
    raw, failure = [], None
    started_at = datetime.now(timezone.utc).isoformat()
    turn_started = time.monotonic()
    first_event, first_text = None, None
    try:
        async with asyncio.timeout(turn_timeout):
            async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=content):
                observed_at = time.monotonic() - turn_started
                if first_event is None:
                    first_event = observed_at
                raw.append(event)
                if (first_text is None and event.author != 'user' and event.content
                    and event.content.role != 'user' and any(
                        p.text and not p.thought and not p.function_call and not p.function_response
                        for p in event.content.parts or [])):
                    first_text = observed_at
                if event.error_code or event.error_message:
                    raise RuntimeError('ADK returned an error event')
            final = next((e for e in reversed(raw) if e.is_final_response()
                          and e.content and any(p.text and not p.thought for p in e.content.parts or [])), None)
            if final is None:
                raise ValueError(f'{case_id}/{index}: no final response')
            final_content = genai.Content(role='model', parts=[genai.Part(
                text=''.join(p.text for p in final.content.parts if p.text and not p.thought))])
            events = [types.evals.AgentEvent(author='user', content=content)]
            for event in raw:
                # Export a copy: signatures are transport details, while raw events stay intact.
                clean = event.content.model_copy(deep=True) if event.content else None
                if clean:
                    for part in clean.parts or []:
                        part.thought_signature = None
                events.append(types.evals.AgentEvent(author=event.author, content=clean,
                    event_time=event.timestamp, state_delta=event.actions.state_delta or None))
            turn = types.evals.ConversationTurn(turn_index=index, turn_id=f'turn_{index}', events=events)
            saved = await runner.session_service.get_session(app_name=app_name, user_id=user_id, session_id=session.id)
            save(output.parent / 'sessions' / case_id / f'state_{index}.json', {
                'session_id': saved.id, 'state': saved.state})
            return final_content, turn
    except BaseException as exc:
        failure = exc
        raise
    finally:
        telemetry = {
            'schema_version':1, 'started_at':started_at,
            'duration_seconds':time.monotonic() - turn_started,
            'time_to_first_event_seconds':first_event, 'time_to_first_text_seconds':first_text,
            'status':'error' if failure is not None else 'success',
            'error_class':type(failure).__name__ if failure is not None else None,
            'event_count':len(raw), 'clock':'client_monotonic',
            'scope':'turn_execution_and_state_snapshot',
        }
        try:
            evidence = {
                'session_id':session.id,
                'user_message':content.model_dump(mode='json', exclude_none=True),
                'events':[e.model_dump(mode='json', exclude_none=True) for e in raw],
                'telemetry':telemetry,
            }
            if getattr(runner, 'transport_stats', None) is not None:
                evidence['transport_stats_after_turn'] = copy.deepcopy(runner.transport_stats)
            save(output.parent / 'sessions' / case_id / f'turn_{index}.json', evidence)
        except Exception as save_error:
            if failure is None:
                raise
            failure.add_note('Could not persist turn evidence: ' + type(save_error).__name__)


async def generate(conversations, output, agent, turn_timeout=180, *, app_name='adk_evaluation',
                   user_id='evaluation_user', initial_state=None, runner_factory=None, agent_factory=None,
                   case_concurrency=1, metadata_only_agent=False, session_timeout=30, cleanup_timeout=5):
    """Keep server-side state; never reconstruct a session from client event actions."""
    if type(case_concurrency) is not int or case_concurrency < 1:
        raise ValueError('case_concurrency must be a positive integer')
    for name, value in [('turn_timeout', turn_timeout), ('session_timeout', session_timeout),
                        ('cleanup_timeout', cleanup_timeout)]:
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{name} must be a finite positive number')
    if case_concurrency > 1 and agent_factory is None and not metadata_only_agent:
        raise ValueError('Concurrent local execution requires agent_factory with fresh agents')
    ids = [c['eval_case_id'] for c in conversations]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Missing or duplicate conversation IDs')
    for c in conversations:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', c['eval_case_id']) or not c['messages']:
            raise ValueError('Invalid conversation')
    journal = output.parent / 'completed_cases.jsonl'
    marker = output.parent / 'generation-started.json'
    if output.exists() or journal.exists() or (output.parent / 'sessions').exists():
        raise ValueError('Generation output must be new; refusing to overwrite evidence')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with marker.open('x') as stream:
            json.dump({'total_cases':len(conversations), 'case_concurrency':case_concurrency}, stream)
    except FileExistsError as exc:
        raise ValueError('Generation output must be new; refusing to overwrite evidence') from exc
    cases, failures = {}, []
    next_index, active, peak_active = 0, 0, 0
    active_agents = set()
    run_id = uuid.uuid4().hex
    statuses = {cid:'not_started' for cid in ids}
    started = time.monotonic()
    shared_metadata = agent_metadata(agent) if agent_factory is None else None
    def progress(complete=False):
        save(output.parent / 'progress.json', {
            'completed_cases':len(cases), 'total_cases':len(conversations),
            'failed_cases':len(failures), 'case_concurrency':case_concurrency,
            'peak_active_cases':peak_active, 'case_statuses':statuses,
            'elapsed_seconds':time.monotonic() - started, 'complete':complete})

    progress()
    async def close_runner(runner, evidence_path, failure, label):
        try:
            async with asyncio.timeout(cleanup_timeout):
                await runner.close()
        except Exception as close_error:
            # Preserve the primary exception, while making cleanup failure durable.
            try:
                save(evidence_path, {'status':'error', 'error_class':type(close_error).__name__,
                                    'timeout_seconds':cleanup_timeout,
                                    'primary_error_class':type(failure).__name__ if failure else None})
            except Exception as save_error:
                (failure if failure is not None else close_error).add_note(
                    'Could not persist cleanup evidence: ' + type(save_error).__name__)
            if failure is None:
                raise
            failure.add_note(f'Could not close {label}: ' + type(close_error).__name__)

    if metadata_only_agent:
        # One read-only readiness check avoids a burst of cold-start metadata requests.
        # This creates no session, executes no agent and performs no retry.
        probe_agent = agent_factory() if agent_factory else agent
        probe = runner_factory(agent=probe_agent, app_name=app_name)
        try:
            async with asyncio.timeout(session_timeout):
                await probe.validate_agent_metadata(agent_metadata(probe_agent))
        finally:
            failure = sys.exc_info()[1]
            await close_runner(probe, output.parent / 'preflight_cleanup_error.json', failure, 'hosted preflight')

    async def execute_case(conversation):
        case_id = conversation['eval_case_id']
        case_user = f'{user_id}_{run_id}_{case_id}'
        case_agent = agent_factory() if agent_factory else agent
        if not metadata_only_agent and id(case_agent) in active_agents:
            raise ValueError('agent_factory reused an active agent; each case requires a fresh agent')
        active_agents.add(id(case_agent))
        runner = None
        try:
            metadata = agent_metadata(case_agent) if agent_factory else shared_metadata
            runner = (runner_factory or InMemoryRunner)(agent=case_agent, app_name=app_name)
            async with asyncio.timeout(session_timeout):
                if hasattr(runner, 'validate_agent_metadata'):
                    await runner.validate_agent_metadata(metadata)
                    if getattr(runner, 'observed_metadata', None) is not None:
                        save(output.parent / 'sessions' / case_id / 'runner_metadata.json', {
                            'metadata':runner.observed_metadata})
                session = await runner.session_service.create_session(
                    app_name=app_name, user_id=case_user, state=copy.deepcopy(initial_state or {}))
            turns = []
            final_content = None
            for index, message in enumerate(conversation['messages']):
                content = genai.Content(role='user', parts=[genai.Part(text=message)])
                final_content, turn = await capture_turn(runner, output=output, case_id=case_id,
                    index=index, session=session, user_id=case_user, app_name=app_name,
                    content=content, turn_timeout=turn_timeout)
                turns.append(turn)
                print(f'{case_id}: turn {index + 1}/{len(conversation["messages"])} complete', flush=True)
            case = types.EvalCase(eval_case_id=case_id,
                **{k: conversation[k] for k in ('prompt', 'reference', 'context', 'rubric_groups') if k in conversation},
                agent_data=types.evals.AgentData(
                    agents=metadata, turns=turns),
                responses=[types.ResponseCandidate(response=final_content)])
            result = case.model_dump(mode='json', exclude_none=True)
        finally:
            failure = sys.exc_info()[1]
            try:
                if runner is not None:
                    await close_runner(runner, output.parent / 'sessions' / case_id / 'cleanup_error.json',
                                       failure, 'evaluation runner')
            finally:
                active_agents.discard(id(case_agent))
        return result

    async def worker():
        nonlocal next_index, active, peak_active
        while next_index < len(conversations) and not failures:
            # No await between dequeue and reservation: bounded workers, no task per case.
            index = next_index
            next_index += 1
            conversation = conversations[index]
            cid = conversation['eval_case_id']
            active += 1
            peak_active = max(peak_active, active)
            statuses[cid] = 'running'
            try:
                result = await execute_case(conversation)
                # These writes do not await; concurrent workers cannot interleave a journal row.
                with journal.open('a') as stream:
                    stream.write(json.dumps(result, separators=(',', ':')) + '\n')
                cases[index] = result
                statuses[cid] = 'completed'
            except BaseException as exc:
                statuses[cid] = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
                failures.append(exc)
                save(output.parent / 'sessions' / cid / 'case_error.json', {
                    'case_id':cid, 'error_class':type(exc).__name__,
                    'status':statuses[cid], 'agent_retry_attempted':False})
                if not isinstance(exc, Exception):
                    raise
            finally:
                active -= 1
                progress()

    workers = [asyncio.create_task(worker()) for _ in range(min(case_concurrency, len(conversations)))]
    try:
        await asyncio.gather(*workers)
    finally:
        for task in workers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        progress()
    if failures:
        raise failures[0]
    save(output, {'eval_cases': [cases[i] for i in range(len(conversations))]})
    progress(complete=True)
