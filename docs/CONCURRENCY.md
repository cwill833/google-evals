# Bounded case concurrency

Set `case_concurrency` in the pack configuration (positive integer, default `1`).
Both local and hosted execution use at most this many active cases per suite in one
process. Suites remain sequential. Turns within a case remain sequential. Each case
gets a separate runner, unique user identity and fresh session, and a deep copy of
initial state. Custom session services must isolate application-scoped state and
external tool resources themselves; user/session isolation is not a tool sandbox.

For local concurrency above one, provide a fresh agent factory:

```json
{
  "agent": "my_agent.agent:create_agent",
  "agent_factory": true,
  "case_concurrency": 8
}
```

The factory must create a fresh agent tree and independent mutable tools per call.
Returning a singleton is not a valid factory; concurrent reuse of the same root is
rejected. Shared subagents, module globals, databases and external side effects cannot
be made isolated by the harness. Hosted local agent objects supply metadata only and
do not require a factory; a single read-only metadata preflight runs before dispatch
to avoid a burst of cold-start requests (no session, agent execution or automatic retry); the server must support isolated concurrent sessions.

The worker pool creates only the configured number of tasks, not one task per case.
Successful output is ordered like the input dataset. `completed_cases.jsonl` is an
append-only completion-order journal, so its order can differ from the final trace.
`progress.json` records configured concurrency, observed peak active cases and each
case's status (`not_started`, `running`, `completed`, `failed`, `cancelled`).
The manifest records the configured concurrency. Turn durations can overlap under
concurrency; summing them is aggregate observed turn time, not elapsed run time. A successful case includes runner
cleanup; a cleanup failure is a case failure.

On the first observed case failure, workers stop taking new cases and drain cases
already in flight. There is no automatic retry of generation or remote POSTs.
Partial events, state, completed journal entries, case errors and progress remain.
No complete trace is emitted for an incomplete suite. The run fails closed and
cannot enter grading-only recovery until generation is complete. Direct coroutine cancellation
cancels workers and attempts cleanup. CLI interruption or command timeout sends SIGINT to
the child process group, allowing the Python workers to persist cancellation evidence and
close runners. The parent waits up to `shutdown_grace` seconds (default 10), then kills
remaining processes; a second interruption skips the remaining grace. A hard kill cannot
guarantee cleanup. Interruption/timeout remains a failed command even if the child exits
successfully during cleanup. Interrupted runs start no additional cases or grading batches.
A generation timeout prevents grading; a grading timeout retains the failed attempt and
may continue other queued batches, without automatically retrying the timed-out batch.

`turn_timeout` covers agent execution and the state snapshot together (default 180 seconds).
`session_timeout` bounds each case's metadata validation plus session creation, and the
hosted preflight (default 30 seconds). `cleanup_timeout` bounds each runner close (default
5 seconds). Cleanup errors are saved separately and never replace an earlier execution
error. Choose a shutdown grace longer than cleanup time if you customize these values.
These are cooperative asyncio deadlines: synchronous blocking or cancellation-resistant
custom code still needs the outer subprocess deadline to terminate it.
A timed-out hosted call may continue remotely; the client records failure and does
not submit it again. The limit bounds active client cases, not orphaned server work.

Google's native metrics and grading are unchanged. All generation finishes before
native grading begins. The separate quota-aware queue still allows one active grading
process per queue, caps grader dispatch QPS, and retains every attempt. `recover`
regrades unavailable pairs from saved traces; it never runs agents or replaces valid
low scores. Original failed runs and reports remain separate and unchanged.

This is asynchronous concurrency in a single process, not a distributed scheduler or
CPU worker pool. Blocking synchronous agent/tool code can stall the event loop. There
is no global agent request-rate limiter across runs; choose a conservative concurrency
for the target's quota. `qps` and `queue_budget_qps` control grading, not agent requests.
The default overall generation timeout stays conservative and is not divided by
concurrency. Persistent hosted sessions, service-account transport authentication and
multi-instance hosting remain outside the verified hosting fixture.

## Repeat verification

`load_test.py --case-concurrency 8 --delay .02 --sizes 100 500 1000` runs real local
ADK conversations with a controlled async delay and explicitly synthetic gate scores.
It measures execution capacity; these scores are not Google quality grades.

`linux_verify_concurrency.py` checks the executing files against `/input/reference.zip`,
installs locked dependencies in a clean Linux environment, runs tests and sequential/
concurrent capacity workloads, then invokes `concurrency_load_test.py`. The latter
executes local and hosted failure scenarios plus 24 local and 100 hosted three-turn
cases with actual Google native grading. It interrupts only the first grader and
checks recovery preserves traces, raw telemetry, prior attempts and original reports.
It requires `/input/endpoint.json`, `/input/deployment-manifest.json`, authenticated
gcloud transport and Google ADC. It incurs real grading cost. Queue files use the
container's native filesystem; evidence is written under `/verification`.

## Local evidence storage

Generated evidence under `artifacts/` stays local and is excluded from source control
and the source ZIP. The standalone load/concurrency scripts can produce fresh evidence
when needed. `linux_verify_hardening.py` runs source-only offline tests and local capacity
checks by default; historical native replay requires `--evidence-root PATH` pointing to
an externally supplied concurrency archive. This separation does not change agent
execution, Google grading, recovery or the contents of previously saved runs.
