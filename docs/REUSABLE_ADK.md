# Reusable Python ADK evaluation

Scope: Python ADK text/tool agents using BaseAgent (including LlmAgent), one final text
response per turn. The live examples use static instruction strings and ordinary Python
function tools. Callable instructions and dynamic toolsets need separate metadata-conversion
validation; the pinned SDK exporter does not establish universal support for them.
No non-ADK integration is included. Google Agents CLI supplies all
semantic grades; local assertions enforce exact domain contracts.

Validate an evaluation pack before cloud calls, then run either example without editing the core:

```bash
uv sync --locked
uv run python -m adk_eval_harness validate --config packs/support/config.json
uv run python -m adk_eval_harness run --config packs/finance/config.json --output artifacts/runs/finance-new
uv run python -m adk_eval_harness run --config packs/support/config.json --output artifacts/runs/support-new
```

Each output directory must be new. Supply your own Google ADC credentials and cloud
project, using .env.example as the configuration guide. These local run commands deploy nothing.
An opt-in hosted ADK transport and private Cloud Run fixture are documented in
[HOSTED_AND_REPORTS.md](HOSTED_AND_REPORTS.md).

An integration consists of a trusted JSON config, a policy, native metric YAML files,
and datasets. Paths are relative to the config; project_root locates your existing ADK
project. Set agent to a module:attribute reference to a BaseAgent, or set agent_factory
true for a zero-argument factory returning a fresh agent per case. The same instance
is retained across turns within that case. Prefer a factory for mutable agent objects
or tools with a connection lifecycle; returning the same global object defeats isolation.
app_name and user_id are configurable.
initial_state supplies a fresh copy for each case. source_roots declares the agent,
tools, fixtures and dependency files to hash; undeclared remote services cannot be pinned.
Keep an agents-cli-manifest.yaml in the project root (see the supplied minimal example).
The harness is a source package run with python -m, not a published PyPI distribution.
For an external checkout, put this package on PYTHONPATH in the same locked environment.

Optional runner_factory is a trusted module:callable accepting agent= and app_name= and
returning an ADK Runner. It must return isolated session services for every case. The
harness uses create_session, run_async, get_session and close. The built-in `hosted`
adapter supports the pinned ADK API-server text/tool protocol with a required metadata
extension; it is mutually exclusive with `runner_factory`. See the hosted guide for
authentication, deployment binding and session limits. Other custom service adapters,
Apps/plugins, voice/media and multi-agent workflows need separate compatibility
verification; they are not certified by these two examples.

Single-turn datasets use the Vertex/Agent Platform EvalCase input format (not ADK's
separate EvalSet export; use the explicit importer below). Multi-turn datasets are lists
of eval_case_id and messages (user text only). A fresh runtime is made per case, including
user-scoped state isolation; all turns in that case share one live session. Raw ADK
events and per-turn session snapshots are retained. Only thought signatures are removed
from our grading copy. The native CLI additionally drops content-less callback events
because its evaluation service rejects them. Their state deltas remain in our original
trace and session snapshots for deterministic checks; do not assume the hosted judge
receives every state-only event. Thought text is not a final answer.

Preflight validates every suite, dataset, assertion and metric before any agent turn.
It prints case/turn/grade counts, requested metric names and local catalog matches,
then checks native agent metadata export. Bare requests use the service default; only
returned native results establish the effective version. Catalog recognition does not
guarantee cloud availability, quota or successful grading. Imports and factories are trusted
local code and can have their own side effects; preflight does not invoke agent turns.
Counts are not a dollar estimate or an underlying judge-request count.
Unsupported native exporter shapes fail early with an explanation.

Policies choose required case/metric coverage, thresholds, metric roles and an optional
assertion_hook (module:function taking case,rules and returning error strings). Hooks
are trusted local code, not remote graders. Unknown rule keys fail validation. A hook
that needs custom rules must explicitly declare them with `check.rule_keys = {"my_rule"}`.
Its implementation is responsible for validating that custom rule's payload and semantics. Built-in checks cover required/forbidden
calls, exact argument subsets, call counts, prerequisite order, tool results, per-turn
JSON and state_equals (exact top-level state keys reconstructed from emitted state deltas).
Initial state is preserved in session snapshots; keys never emitted in a state delta
are not visible to the trace-only state_equals check. This assertion does not verify
persisted session state or detect divergence between a delta and the saved snapshot.
Snapshots are preserved and integrity-checked for inspection; snapshot-backed policy
assertions are not implemented. JSON comparisons distinguish booleans from numbers,
including nested values; integer and decimal representations of the same JSON number
compare numerically. Finance authorization/ledger logic
lives in examples/finance; support approval logic lives in examples/support.

The example tools are offline simulations. The harness does not automatically sandbox
arbitrary ADK tools. Supply an agent factory with isolated test tools before evaluating
agents with side effects. No production credentials are needed by either example's tools.

Freeze files bind datasets, metrics and policies before a holdout run. All imports and
configuration files must be trusted. Reports bind traces, scores and policy by hashes.
Evaluator failures block even if the affected metric's quality role is advisory.
See CALIBRATION.md for the distinction between native scores and release policy.

Legacy scripts/run_evals.py and scripts/quality_gate.py remain finance compatibility
entry points for replaying historical evidence. New integrations use adk_eval_harness.

## Minimal configuration for an existing ADK project

Save this alongside that project's agents-cli-manifest.yaml, then replace the import and
file paths with your project's names. No harness source edit is required.

```json
{
  "schema_version": 1,
  "framework": "adk",
  "project_root": ".",
  "agent": "my_agent.agent:root_agent",
  "app_name": "my_agent",
  "user_id": "evaluation_user",
  "source_roots": ["my_agent", "pyproject.toml", "uv.lock"],
  "policy": "evals/policy.json",
  "suites": {
    "smoke": {
      "dataset": "evals/cases.json",
      "metrics": "evals/metrics.yaml"
    }
  }
}
```

Use packs/support as a complete worked evaluation pack: copy its policy/data structure,
replace expected ticket behavior with your agent's requirements, and select applicable
native metrics. If you need domain rules beyond the generic assertions, supply your own
trusted assertion_hook. Do not carry finance or ticket assertions into an unrelated agent.
The config and policy must agree exactly on suite IDs, case IDs and selected metrics.

Run the module from an environment containing the pinned harness dependencies and your
agent's dependencies, with the reference checkout on PYTHONPATH. Conflicting dependency
versions require a separately verified lockfile/compatibility update. The current gate
fails closed on versions different from those tested here.

## Importing an existing ADK EvalSet

ADK's `adk web` / `adk eval` EvalSet format (`eval_id`, `conversation`) differs
from the Vertex EvalCase format (`eval_case_id`, `prompt`) used by the managed
Google grader. The importer accepts snake_case field names; camelCase alias exports
are rejected. Convert an exported static text EvalSet into a **draft** pack:

```bash
uv run python -m adk_eval_harness.import_adk /path/to/agent.test.json --output /path/to/new-pack
```

The output contains `dataset.json`, native `metrics.yaml`, `policy.json`, a
`config.fragment.json`, all expected replies in `expected-responses.json`, the
unchanged source export, and a conversion report with its SHA-256. Merge the
fragment into the configuration above; set your agent import, project path and
source roots, and resolve pack paths relative to your complete config. Review
the generated requirements and threshold before running the harness. Import
never executes the agent or calls a hosted grader and never overwrites a pack.

The importer replays **user turns only** against a fresh live agent session.
Expected assistant replies are references, never fabricated session history.
The final expected reply becomes a native reference; earlier expected replies
are retained for review but are **not individually scored**. Expected tool calls
and results become per-turn argument/result subset checks and call counts.
Those checks do not enforce exact tool argument objects or the original call
ordering. Empty `tool_uses` does not prohibit calls, extra tool names remain
unconstrained, and expected calls/results are not paired by their original IDs.
Add explicit assertions for these requirements before using the draft as a gate.
The suggested `multi_turn_task_success` metric and 0.8 threshold are
starting points for calibration, not a translation of prior ADK metric scores.
Invocation IDs, timing and app metadata remain in the source export rather than
scoring inputs. The conversion report lists these differences explicitly.

The importer rejects media, dynamic conversation scenarios, ADK custom rubrics,
intermediate agent responses, full invocation-event exports, fixed session IDs
and expected final session state, including an explicitly supplied empty object.
These need deliberate adapters/assertions; the importer never treats an explicit
empty-state expectation as permission to ignore state. A missing or null
`final_session_state` supplies no final-state requirement.
Session app, user and initial state must agree across cases; otherwise split
the source into separate packs. Arbitrary extra semantic fields are rejected
rather than discarded. This importer covers the static text subset; it does
not claim interchangeable grading with every ADK evaluation format.

## Large suites and evidence replay

Generation defaults to one case at a time. Set `case_concurrency` to a positive integer
for bounded asynchronous local or hosted execution. Local concurrency above one requires
`agent_factory: true` and a fresh agent tree per case. The configured `user_id` is a
prefix for unique case identities, keeping user-scoped state isolated even with shared
session services. Turns stay sequential. See [CONCURRENCY.md](CONCURRENCY.md) for failure,
readiness and recovery behavior. `turn_timeout` includes execution and state retrieval
(default 180 seconds). `session_timeout` bounds metadata/session creation (30 seconds),
`cleanup_timeout` bounds runner close (5 seconds), and `shutdown_grace` allows CLI child
cleanup before forced termination (10 seconds). All four accept finite positive seconds.
These deadlines require cooperative async code; the overall
generation budget defaults to 60 + total turns × turn_timeout, so hundreds of valid turns
are not arbitrarily cut off at 30 minutes. Set `generation_timeout` for a stricter budget.
`grading_timeout` defaults to `command_timeout` (1,800 seconds); increase it explicitly
for slow/large cloud runs. `qps` defaults to a requested 2 case-metric computations per
second. The shared one-host grading queue caps effective dispatch at `queue_budget_qps`
(default 0.5); cooperating runs must use the same `grading_queue` directory, including
across project roots. Use a native local POSIX/SQLite filesystem, not a Docker Desktop
host bind mount. This is not a distributed quota coordinator and cannot account for
unrelated clients or native SDK fan-out/retries.
The native SDK controls its own service retries; the harness never retries a low score
or a partially executed conversation. Timeout signals the subprocess group, permits
bounded cleanup, then kills any remaining descendants and fails the evaluation. Missing/error results remain failures, including for advisory metrics.

Completed cases are appended once to `completed_cases.jsonl`; `progress.json` records
completed/total counts. A failed turn retains partial events. Generation refuses to reuse
a started output directory even if the first case failed. These checkpoints are forensic
evidence, not an automatic resume mechanism. A new run needs a new directory. Traces
and native grade results remain materialized in memory; this is not a streaming or
distributed evaluator. See REVIEW_AND_LOAD.md for measured capacities and boundaries.

`check --config CONFIG --output RUN` verifies the current config, policy, agent and
declared source hashes against the evaluated run before replaying its gate. Old or
different-agent evidence cannot approve current code. Historical evidence can still
be inspected with `python -m adk_eval_harness.gate RUN --policy RUN/policy.json`; that
command replays the saved policy using currently available trusted hooks and explicitly
does not certify current-source identity.

Native metric names may be pinned, for example `final_response_match_v3`, in both
metric YAML and policy. A pinned request rejects results for another version. Bare
names use the SDK/service default and report the emitted native version. The local
catalog's newest version is a candidate, not a guarantee of that default. Client
version pins do not freeze hosted judge behavior.

## Shared grading queues and recovery

All ADK packs use the same queue/attempt implementation. Configure `grading_queue` to
the same local POSIX directory for runs sharing a cloud quota, including across different
project roots. Default dispatch budget is 0.5 case-metric computations per second,
batches contain at most 100 cases and one native metric, and unavailable pairs have at
most three recorded attempts. The defaults are configurable before a run.

After complete generation, use `recover --from-run SOURCE --output NEW --config CONFIG`
to append grading recovery without agent execution. Valid grades, including zero, cannot
be retried; low quality stays visible. Missing/error grades remain blocking until resolved.
See [configuration, commands and immutable evidence rules](QUEUE_AND_RECOVERY.md).

## Reports and telemetry

Run finalization writes `report.html` and `report.json`. Use
`python -m adk_eval_harness report --output RUN` to regenerate an existing report offline.
It replays saved evidence/policy integrity and shows actual prompts, turn replies, tools,
native grades/explanations and recovery history. Partial generation remains ungraded.
Raw event/state snapshots are linked and hash-checked when the run recorded telemetry
bindings. Older evidence is labelled unbound; missing timing/token values are unavailable.
Agent token observations and client-measured turn latency are separate from judge usage,
which is unavailable unless explicitly supplied by native results. See
[report files, measurements and hosted limitations](HOSTED_AND_REPORTS.md).
