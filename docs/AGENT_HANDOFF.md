# Agent handoff: compare ADK evaluation platforms

## Purpose and scope

Compare this evaluation platform with your existing implementation and recommend which
designs to keep, combine or change. The target workflow is: bring an ADK agent and its
datasets, evaluate locally, iterate on the agent/evals, deploy using an existing pipeline,
then evaluate the hosted agent with the same cases, native metrics and policy.

Review the evaluation system. The finance, support and load agents are fixtures for
proving reuse, state handling and failure behavior; their domain sophistication is not
the product. Deployment helpers are optional verification fixtures. Non-ADK adapters are
outside the current target. This is a reference POC, not a claim of universal ADK support
or a statistically calibrated production release gate.

## Pasteable task for the receiving agent

```text
Compare your existing ADK evaluation implementation with this reference POC:
https://github.com/cwill833/google-evals/pull/1

Check out the PR branch codex/adk-evaluation-reference and record the commit SHA
of both implementations. Read README.md and docs/AGENT_HANDOFF.md first. Use the
offline quickstart, inspect the actual code/tests, and compare the evaluation
platforms using the criteria in the guide. Focus on bringing an existing ADK
agent and datasets, local/hosted parity, Google's native metrics, isolated
concurrency, quota handling, grading-only recovery and auditable results.

Distinguish implemented, tested, historically measured and unverified behavior.
Synthetic fixtures are not live Google grades. Raw historical runs are retained
locally and absent from the PR; request specific evidence if needed. Do not treat
a missing archive as proof that a documented live run did or did not happen.

Return an evidence-backed comparison table, prioritized findings with file/line
references and reproductions, useful design choices from each implementation,
and a concrete recommendation on what to adopt or change before showcasing it.
Do not assume either implementation is better. Keep this pass read-only except
for temporary test outputs; propose changes before implementing them. Do not run
billable evaluations or deploy resources as part of this offline comparison.
```

Give that task to an agent with access to your other implementation. If it does not have
that context, provide its repository/branch and the scope of comparison alongside this PR.

## First run and reading order

Follow the [README offline quickstart](../README.md#start-here-use-or-compare-this-poc).
The tests use real local ADK components where relevant and synthetic native-shaped grade
fixtures. No historical archive or cloud credentials are required. After installation,
record `git rev-parse HEAD`, Python/platform details, test output and pack validation output.
Importing a user-supplied agent/config is executing trusted code; the supplied example
preflight invokes no agent turns or cloud grader.

Read these in order:

1. [Architecture](ARCHITECTURE.md): boundaries and execution flow.
2. [ADK integration](REUSABLE_ADK.md): pack schema, external project setup, factories,
   dataset formats, explicit ADK EvalSet conversion and assertion semantics.
3. [Concurrency](CONCURRENCY.md) and [queue/recovery](QUEUE_AND_RECOVERY.md): independent
   execution/grading controls, failure handling and recovery limits.
4. [Hosted targets and reports](HOSTED_AND_REPORTS.md): the required server contract,
   authentication, evidence files and telemetry definitions.
5. [Calibration](CALIBRATION.md), [metric inventory](METRIC_INVENTORY.md) and
   [upstream issues](UPSTREAM_ISSUES.md): native score interpretation and known constraints.
6. [Hardening](HARDENING.md) and [results](RESULTS.md): review fixes, historical measurements
   and what was actually verified. Upstream issue notes are recorded research, not a
   guarantee of their current resolution status.

## Follow a run through the code

| Boundary | Code to inspect | Review question |
| --- | --- | --- |
| Pack/preflight | [config.py](../adk_eval_harness/config.py), [validation.py](../adk_eval_harness/validation.py), [support pack](../packs/support/config.json) | Can a new agent supply data, metrics and policy without modifying the harness? |
| Dataset import | [import_adk.py](../adk_eval_harness/import_adk.py) | Are unsupported shapes and changes in assertion semantics explicit? |
| Agent execution | [generation.py](../adk_eval_harness/generation.py), [remote.py](../adk_eval_harness/remote.py) | Are turns sequential within each isolated case, with bounded concurrency across cases? |
| Run orchestration | [runner.py](../adk_eval_harness/runner.py) | Are generation, grading, checking, reporting and recovery distinct actions? |
| Native grading admission | [grading_queue.py](../adk_eval_harness/grading_queue.py) | What does the shared quota budget bound, and what happens on process death or throttling? |
| Grading attempts/recovery | [attempts.py](../adk_eval_harness/attempts.py), [lineage.py](../adk_eval_harness/lineage.py) | Are valid scores sticky and failed attempts retained without agent reruns? |
| Release decision | [gate.py](../adk_eval_harness/gate.py), [example domain assertions](../examples/support/assertions.py) | Are native scores kept separate from coverage, deterministic contracts and policy roles? |
| Evidence/reports | [integrity.py](../adk_eval_harness/integrity.py), [reporting.py](../adk_eval_harness/reporting.py) | Do check, recovery and report agree on missing/corrupt evidence? |

The run path captures agent traces before native grading. Google Agents CLI supplies
semantic scores; the harness does not implement a replacement LLM judge. It applies
coverage checks, deterministic assertions and configured blocking/advisory thresholds,
then writes a manifest, gate and HTML/JSON reports in the output directory. New output
directories preserve earlier runs. The older finance scripts remain compatibility paths;
use `python -m adk_eval_harness` for new integrations.

## Comparison criteria

For each row, report this implementation, your implementation, evidence, practical impact
and a recommendation. Mark unsupported or untested behavior explicitly.

| Area | Compare |
| --- | --- |
| Reuse | New external ADK agent onboarding, dependency conflicts, factory/runner contracts, domain rules outside the core |
| Datasets | Native input formats, EvalSet import fidelity, multi-turn expectations, metric applicability, negative controls, frozen regression sets |
| Local/hosted parity | Shared cases/policy, deployment identity, required endpoint extensions, transport authentication, session persistence and timeout semantics |
| Execution | Case isolation, mutable tools/global state, bounded concurrency, deadlines, interruption, partial evidence and external side effects |
| Grading | Native metric/version provenance, coverage validation, low score versus unavailable grade, advisory versus blocking policy |
| Recovery | Agent invocation count, unchanged trace bytes, valid low score preservation, attempt limits, parent lineage and concurrent writers |
| Quotas/capacity | Agent concurrency versus judge dispatch, queue scope, backpressure, throttling, hundreds/thousands of cases, real versus synthetic grading load |
| Evidence | Prompts/replies/tools/state, raw artifacts and hashes, failed runs, reproducible gate checks, report escaping and sensitive-data handling |
| Telemetry | Observed token fields, missing values, agent versus judge usage, latency definitions, cost and budget enforcement |
| Operations | CLI exit semantics, offline report/replay, package reproducibility, CI integration, storage/dashboard integration and retention |

Meaningful negative checks should establish that failures remain failures:

- A missing grade or grader error blocks even when the metric's quality role is advisory;
  a valid low score cannot be retried into a pass. See [attempt gates](../tests/test_attempt_gate.py)
  and [recovery contracts](../tests/test_recovery_contract.py).
- Corrupt/missing traces, telemetry or ancestry cannot produce a clean gate/report or
  recovery. See [integrity](../tests/test_gate_integrity.py),
  [lineage](../tests/test_lineage.py) and [reporting](../tests/test_reporting.py).
- A generation failure stops new work, preserves partial evidence and cannot be recovered
  as a grading-only failure. Cases do not share sessions. See
  [concurrency](../tests/test_case_concurrency.py), [timeouts](../tests/test_runner_timeout.py)
  and [remote transport](../tests/test_remote.py).
- Boolean/number mismatches and null-valued tool payloads retain their JSON semantics.
  See [review regressions](../tests/test_review_hardening.py).

## Evidence and limits to carry into the recommendation

The source-only verification recorded **327 tests passing on macOS and clean Linux ARM64**.
Historical live runs include local and hosted Google grading plus grading-only recovery;
their measurements and original failures are in [RESULTS.md](RESULTS.md). The current PR
does not contain those raw runs. Offline unit tests cannot independently reproduce those
live measurements. Ask for a specific run and its lineage if the comparison needs proof
beyond code and recorded summaries.

The 1,000-case capacity check exercised local ADK sessions/tools with synthetic grades.
It does not establish 1,000-case cloud judge throughput or real LLM latency. The local
and hosted native grading runs were smaller and are documented separately.

Known boundaries to assess rather than overlook:

- Verified Python ADK text/function-tool scope with pinned Agents CLI 1.11.0 and ADK 2.12.0.
  These are different version numbers. Older ADK runtimes, arbitrary toolsets, media and
  complex multi-agent workflows need separate compatibility validation.
- Hosted use requires the documented API and `/eval/metadata` extension, matching local
  metadata and the verified authentication path. The demo deployment's single-instance,
  in-memory sessions are not a production hosting design or remote attestation.
- The quota queue coordinates one host; it is not a distributed scheduler. Isolation of
  sessions does not sandbox shared tools, global state or real-world side effects.
- `state_equals` checks emitted state deltas, not persisted snapshots. Snapshots are saved
  and integrity-checked, but snapshot-based policy assertions are not implemented.
- Token usage is reported only when supplied by the agent/provider. Judge tokens, billing
  totals and dedicated tool latency are not generally available. Latency is measured at
  the client; first text is not guaranteed true time-to-first-token. Token/latency budget
  gates are not implemented.
- Runs are stored as filesystem directories. There is no built-in GCS run store, central
  dashboard, distributed run database or automatic retention policy. HTML/JSON reports
  are custom views over native results, not Google's out-of-the-box report UI.
- The small calibration corpus and published holdouts support regression checks, not an
  unseen statistical benchmark or general agent-quality claim.

Conclude with separate judgments on demonstration readiness, reuse for the target agent,
and production rollout. For every proposed change, identify whether it belongs in this
harness, the agent integration, deployment infrastructure or an upstream Google component.
