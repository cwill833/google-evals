# Evaluation platform review and capacity testing

The evaluation platform is the product; agents in this repository are fixtures. This
pass reviews platform correctness, Google-native integration, evaluation methodology,
ADK onboarding and capacity. No financial/support agent prompts or tools were tuned.

## Independent reviews and fixes

Three parallel code-review agents reviewed correctness/evidence integrity, Google and
evaluation practices, and ADK onboarding. A second review checked the new dataset importer
and version-pinning implementation. These are automated reviews, not independent human
security audits or a Google certification.

| Finding | Resolution |
|---|---|
| `check` could approve finance evidence with a support config | Bind current config, policy, agent and declared source hashes |
| Invalid packs consumed agent calls before validation | Whole-pack preflight with applicability, required fields and exact coverage |
| Misspelled policy checks silently disappeared | Strict policy/assertion schema, explicit hook extension keys |
| Candidate answer could disagree with observed response | Bind final candidate to actual non-thought response |
| Missing tool field matched explicit null | Require field presence as well as equality |
| Duplicate call IDs and thought text weakened assertions | Reject duplicate IDs; ignore thought text in answer assertions |
| Metric versions obscured or impossible to pin | Preserve native names and enforce explicit requested versions; distinguish local catalog candidates from service defaults |
| Whole suite stopped at 30 minutes regardless of case count | Scale generation deadline by turns; configurable grading/command budgets |
| All completed traces rewritten after each case | Append one checkpoint per case; atomic final/progress files |
| First-case failure evidence could be overwritten | Exclusive generation marker and refusal to reuse started output |
| Mutable agent/tool lifecycle leaked between cases | Fresh agent factory per case, same instance across that case's turns |
| Native ADK datasets required manual reconstruction | Explicit static-text EvalSet import into a reviewed draft pack |
| Unsupported native metadata failed after execution | Native exporter compatibility check before any agent turn |

## Load-test design

Local capacity workloads execute a real deterministic ADK BaseAgent and a real Python
tool with session state: 100, 500 and 1,000 cases, three turns each, 1,024-byte payloads.
Each case must start at counter 1 and reach 3. The harness writes raw events, state
snapshots, a linear completion journal and typed native traces. Synthetic scores are
used **only** to benchmark local gate processing and fault controls; they are not Google
quality evidence. Missing cases, injected grader errors and low grades must be rejected,
and infrastructure faults must not count as successful negative-agent detection.

A separate cloud workload executes 200 one-turn cases with a 128-byte payload and uses
Google's unchanged native final_response_match_v2 metric. The initial workload used
qps=2; the revised ordinary-text workload uses qps=0.5. The fixture has no
model calls. This measures actual native grading integration at hundreds of cases; it
does not measure arbitrary LLM-agent latency or all multi-turn judge workloads.
A fresh support-agent evaluation covers real model generation and three native
multi-turn metrics. The prior finance/support/control evidence is replayed independently.

Reproduce local load without cloud access:

```bash
uv run python scripts/load_test.py --output artifacts/runs/load-new --sizes 100 500 1000
```

Explicitly run the billable native load:

```bash
uv run python scripts/load_test.py --native --sizes 200 --payload-bytes 128 --qps 0.5 --output artifacts/runs/native-load-new
```

Clean Linux package verifier: `scripts/linux_verify_load.py --native-cases 200 --native-qps 0.5 --support`.
It installs the locked dependencies from the extracted public archive, runs the test
suite, local capacity tests, native cloud load, fresh support evaluation and historical
controls. Use the read-only archive/ADC mounts documented in STATEFUL_PASS.md and change
the verifier filename. Without the cloud flags, it runs local tests only.

## Boundaries

- This historical pass used sequential generation. [Bounded concurrency](CONCURRENCY.md)
  now supports local and hosted case workers; automatic generation resume and a
  distributed scheduler remain unimplemented. The
  subsequent [queue/recovery pass](QUEUE_AND_RECOVERY.md) adds a shared one-host queue
  and explicit recovery of unavailable grades; cooperating runs must share its path.
- Native SDK grading materializes traces/results in memory. Peak RSS includes the
  workload's synthetic fault-injection copies; it is a conservative worker footprint,
  not just core harness memory. Payloads and longer conversations change resource use.
- Cases/grades are not underlying judge calls or dollar cost. Google's multi-turn
  metrics can invoke multiple judge calls per grade. QPS/quotas and hosted model latency
  are external limits; timeout/retry behavior and reporting are platform responsibilities.
- New projects need their own representative dataset, expected behavior and calibrated
  gate. The example advisory policy is not a universal release policy. It is small,
  requirement-driven engineering calibration, not statistical human-labeled validation.
- Compatibility is verified for Python ADK static text/function-tool agents. Callable
  instructions, dynamic toolsets, Apps/plugins, media, remote runners and complex
  workflows require further integration validation. No claim about the percentage of
  all ADK agents is supported by these fixtures.
- Verification uses ARM64 Linux userspace under Docker Desktop and existing credentials.
  It does not prove x86_64/Windows, a physical Linux host or fresh-account IAM onboarding.

Primary references: [Google CLI evaluation](https://google.github.io/agents-cli/guide/evaluation/),
[Google metric versions and pipeline](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/rubric-metric-details),
[Google evaluation quotas](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/quotas).

## Measured Linux results

Final candidate: **129 tests passed** in a clean locked environment. All four local
workloads passed, including all 12 injected missing-result, unavailable-grade and
low-score controls. Each is one measurement, not a latency SLA.

| Cases | Turns | Payload bytes | Preflight + generation | Gate | Peak worker RSS |
|---:|---:|---:|---:|---:|---:|
| 100 | 300 | 1,024 | 2.14 s | 0.02 s | 367 MiB |
| 500 | 1,500 | 1,024 | 4.41 s | 0.22 s | 451 MiB |
| 1,000 | 3,000 | 1,024 | 7.37 s | 0.49 s | 560 MiB |
| 500 | 2,500 | 4,096 | 6.87 s | 0.40 s | 707 MiB |

The larger-payload workload wrote 53.6 MB of native traces and
226.3 MB of total local artifacts. Peak memory includes
synthetic result/fault copies as well as the actual ADK runtime. The synthetic scores
are only gate-load inputs, not Google quality evidence.

The corrected 10-case cloud diagnostic passed, followed by **200/200 native grades
at 1.0**, with no service errors and 0 retry messages. The 200-case run took
**6.99 minutes**, using explicit `final_response_match_v2` at qps=0.5.
These are real Google grades over executed ADK traces, not synthetic benchmark scores.

The fresh support evaluation passed its existing policy: **8 cases, 20 valid native
grades**, with all six multi-turn task-success grades at 1.0. Two native warnings remain:
`close_then_read` trajectory **0.625**, and `cancel_then_read` tool-use **0.5**. Their
saved rubrics contain respectively three and three conditional criteria marked null,
while the triggered criteria pass. Scores and thresholds remain unchanged. The earlier
finance/support/control native evidence also passed the current offline verifier with
all six faulty cases still correctly rejected.

### The first load did fail, and remains preserved

The initial 200-case placeholder-text run at qps=2 had **147 scores of 1.0, 34 scores
of 0.0, and 19 parsing errors**, despite exact response/reference equality in all cases.
It logged 85 SDK retry messages for judge capacity. The judge interpreted the repeated
`x` content as masked data; no test content was actually redacted. The gate correctly
rejected it as incomplete. We changed the fixture to ordinary text, added an explicit
user task, and reduced dispatch; the following runs are not controlled experiments
isolating which change caused improvement. Explicit v2 pins the same native metric
version observed in the original run. No metric/threshold was replaced or weakened.

All initial failures, subsequent native scores, rubric reasons and manifests are in
`artifacts/evidence/review-load`. Original trace/result files were exported byte-for-byte.
Large local synthetic artifacts remain under `artifacts/runs` and can be regenerated.

### Package provenance and readiness

Candidate one SHA256: `2d5260d7fd5e964671cf5709926010b9f0a2c6895ca205bd2885d7f986b1f47c`.
Candidate two SHA256: `1b1c7fd661c2e9fec8325bc20a05ecf0ae245cd7754ce7567425afd776f616b6`.
Support was executed from candidate one; candidate two corrected the preflight version
report and load fixture/dispatch controls. Agent generation and gate code are identical
between the two. Candidate two supplies the final source baseline; final-archive source
identity and privacy checks are recorded in the adjacent `.verification.json` sidecar.

The reference is ready for documented Python ADK text/tool evaluation and hundreds-case
pilots within the measured workload sizes. This does not establish universal ADK
compatibility, sustained multi-tenant throughput, a production SLA or arbitrary native
multi-turn grader capacity. Before production scale, add workload-specific latency/cost
budgets and distributed operational validation. The subsequent [queue/recovery pass](QUEUE_AND_RECOVERY.md)
implements the one-host quota queue and explicit failed-grading recovery identified here.
Never retry low scores until green or discard incomplete native results.

