# Hardening and publication scope

The finance contract below remains in force. The reusable pass adds a separate support
example, shared ADK runtime and policy core, per-agent assertion hooks, and explicit native
metric roles. See REUSABLE_ADK.md, CALIBRATION.md and RESULTS.md for the current package.
Earlier stateful results below are historical, not the latest verification verdict.

## Final platform review fixes — 2026-10-22

The final independent review and follow-up reproductions are resolved:

- Gate, CLI check, report and grading-only recovery share raw-telemetry integrity checks.
  Modified/missing bound artifacts and added unbound session JSON files fail closed.
  Historical runs without telemetry bindings remain explicitly labeled unbound.
- Exact deterministic comparisons distinguish JSON booleans from numbers, including
  nested responses, tool arguments/results and state values.
- Graded-dataset comparison normalizes only optional SDK schema fields. Null values
  inside tool payloads, state deltas, metadata and JSON Schema remain significant.
  The native CLI's documented removal of content-less callback events remains an
  upstream projection limitation; original events and snapshots remain available.
- State retrieval shares the turn deadline. Session setup and runner cleanup have
  separate bounded deadlines; cleanup failures retain the primary execution error.
- CLI cancellation allows bounded cooperative cleanup, persists partial evidence and
  retains failure status. Uncooperative descendants are killed after the grace period.
- Public packaging rejects symlinked files and directory ancestors before writing.

See CONCURRENCY.md for deadline defaults and cancellation limits. State assertions
still evaluate emitted deltas, not persisted snapshots. Published holdouts are now
regression coverage; new generalization claims need new independently reviewed cases.

Verification uses the extracted public package in clean Linux ARM64 with locked
Python 3.12 dependencies, without cloud credentials. It runs the complete test suite,
100/1,000-case local capacity workloads and, when supplied separately, offline replay
of the prior native Google results. Raw evidence and archive sidecars remain local. This pass does not tune agents, change native metrics, rerun hosted agents,
or claim new cloud quality measurements. Results and provenance are in RESULTS.md
and `artifacts/evidence/final-hardening/`.

Repeat with `scripts/linux_verify_hardening.py` using `/input/reference.zip` and a
fresh `/verification` output mount. It installs dependencies from package indexes;
subsequent workloads use local deterministic agents. To include historical replay,
mount the local concurrency evidence separately and pass `--evidence-root PATH`.
`--replay --evidence-root PATH` performs only replay in an already installed environment.
An explicitly requested but missing archive is an error; it is not silently skipped.

## Transfer contract

The sandbox maintains balances, transaction history, transfer IDs, notification records,
and cumulative transfer totals in ADK session state. C001 has a combined outgoing limit
of 50,000 cents per UTC calendar day across both owned accounts. Transfers in either
direction count toward that limit. A successful transfer debits and credits the session
ledger, returns updated balances, and receives a unique ID. Rejected transfers leave the
ledger unchanged and consume matching authorization. Notification recording is idempotent.

This limit is explicitly **session-local**. New sessions start from the fictional fixtures;
there is no durable database, authenticated customer identity, cross-session limit, or
concurrency-safe banking service. Tools execute synchronously within a serialized session;
concurrent independent invocations against the same session are outside this contract.
This is an evaluation example, not financial infrastructure.

Local tests include two authorized $400 requests, exact-limit and over-limit requests,
reversed account direction, UTC day rollover, depleted balances, independent sessions,
and duplicate notifications. An actual ADK Runner test reuses one session for two turns
and verifies that the second $400 transfer is denied and persisted balances remain correct.
This integration test is deterministic; it does not call Gemini or claim a Google grade.

## Negative evidence

`--fault-injection` selects a separate project under `tests/fault_project`. The normal
Gemini agent has no fault toggle. The deliberately faulty ADK agent executes real sandbox
tools and emits actual ADK events through native CLI generation. Its behavior is scripted,
not Gemini-generated, and the resulting traces are not edited to manufacture failures.
Google's managed reference-free metric grades every case. The independent gate must reject:

- A balance lookup for A200 when A100 was requested.
- A transfer that skips balance and policy prerequisites.
- An unauthorized transfer attempt, even when the tool safely rejects it.
- A JSON success claim when no transfer occurred.

The separate verifier checks that all four native grades exist, native commands succeeded,
and each intended deterministic failure is present. Infrastructure failures do not count as
successful fault detection. A fault run is always a failing release-gate run, never a pass.

The release corpus also includes quoted authorization, conflicting amounts in one message,
and a request above the limit. The original wrong-reference negative control remains separate.

## Evaluation limits

The stateful pass replaces the earlier restarted-session conversation adapter with one
live ADK session per case. Actual state deltas, tool events and post-transfer reads now
span turns; Google’s native grading remains unchanged. Nine conversations include a
four-case prospective holdout frozen before live execution. See STATEFUL_PASS.md for
Linux verification and the latest execution status.

There are few cases per metric, no statistical confidence estimates, and no production
threshold calibration. Earlier prompt/metric changes were informed by observed failures, so most
of the corpus is development coverage. The new holdout was frozen before execution and
was not used for prompt tuning; after inspection it becomes public regression coverage. Hosted model
and evaluator behavior can vary even with pinned client dependencies and temperature zero.
The earlier 0.875 trajectory grade included null verdicts for inapplicable error conditions;
read the saved rubric reasoning alongside numeric grades. Two exploratory chat-only metrics
had means below 0.8 and are not counted as release passes. They omit tool observations.

Failure fixtures return controlled error dictionaries; they do not exhaustively simulate
network outages, concurrent writes, arbitrary prompt injection, or real service permissions.
The reusable command runner now sends SIGINT on timeout/interruption, allows bounded
cleanup, then kills remaining descendants. Tests cover actual CLI cancellation, evidence
flush and uncooperative processes. The legacy finance runner still kills immediately. Optional synthesis/analysis and media metrics remain
unexecuted or inapplicable as described in METRIC_INVENTORY.md.

## Public distribution

Project code is distributed under Apache-2.0; dependencies retain their own licenses.
The allowlist excludes credentials, local state, environments, and private raw runs.
Exported logs redact the home/workspace prefix and configured Cloud project identifier.
The archive checker includes embedded DOCX XML, selected credential patterns and local
identifier checks; this is a bounded check, not a comprehensive security audit.

A fresh run must come from an extracted archive, install `uv.lock`, execute without trace
reuse, and preserve release and negative results. Any final evidence/documentation refresh
must leave the tested source/data/lockfile hashes unchanged. See RESULTS.md for actual status;
this document does not assert that a pending run passed.

## Fresh-package defect found during hardening

The first clean-package run returned “The balance for account A100 is $1,250.50.”
The authored rubric required an explicit USD currency label and assigned 0.0. The agent
instruction was corrected to include USD in prose balance answers. The case, rubric,
and threshold were retained. This failed run is preserved separately; verification of
the corrected source requires another complete fresh-package run, without trace reuse.

A second clean-package run passed the currency rubric but exposed a context mismatch:
the policy answer named USD while the policy text only named “$500”. Both the source
policy and the grounding context now explicitly name USD. The same run's other grounding
case returned score 0 with “No sentences found in grounding response” despite a normal
sentence in the trace. The gate preserves and rejects that result rather than treating it
as a pass. This service anomaly is not an agent-correctness finding.

The first fresh run also scored transfer trajectory at 0.75 because four generated
criteria concerned conditions that never occurred (fraud, errors, conflicting records,
and JSON-only output). A separate native probe supplied six authored trajectory criteria
through `rubric_group_key`; the service returned 19 generated rubrics instead of the six supplied criteria, so the
probe did not establish a supported case-specific override. Its 0.9474 score is research
only and does not replace the failed release grade. The native metric and 0.8 threshold
remain unchanged. False-positive release failures remain a material upstream limitation.
The [Google metric reference](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/rubric-metric-details)
describes trajectory grading as adaptive rubric generation and validation.

The previous hardening verification used a fresh extraction on the same macOS arm64
host. The stateful pass adds a fresh Linux container, network dependency installation and
live evaluation; see STATEFUL_PASS.md for its actual result. Neither proves cross-account
access or separate physical-host compatibility. The runner requires Bash and POSIX process groups; native
Windows execution is unsupported. Dependency installation uses the locked local package
cache, while agent inference and managed grading make real Google calls.


The third package's first run suffered a native `/run_sse` read timeout during the
transfer conversation. The CLI returned only the other case; the runner rejected the
missing case and did not grade the incomplete multi-turn suite. One full no-reuse retry
of the same final package followed as a transport retry after other cloud jobs
finished. The original failed run is retained. This is not a low-score regrading strategy.
