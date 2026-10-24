# Reusable Google ADK evaluation reference

A configuration-driven evaluation harness for Python ADK agents. Add an agent and its
datasets, evaluate locally, iterate, then evaluate the deployed agent using the same
datasets and policy. Your existing pipeline owns deployment. Google supplies every
semantic grade; this harness adds execution, coverage checks, release policy, recovery
and evidence reports. Finance and support agents are examples used to test the platform.

## Start here: use or compare this POC

For an agent-to-agent implementation comparison, read
[the handoff and comparison guide](docs/AGENT_HANDOFF.md). It includes a pasteable task,
code map, comparison criteria and the limits of the available evidence.

To review [PR #1](https://github.com/cwill833/google-evals/pull/1), check out its branch
(the work is not yet merged into `main`):

```bash
git clone --branch codex/adk-evaluation-reference https://github.com/cwill833/google-evals.git
cd google-evals
uv sync --locked --python 3.12
uv run pytest -q
uv run python -m adk_eval_harness validate --config packs/support/config.json
```

These tests and the supplied pack's validation need no cloud credentials, deployment or
historical run archive. Dependency installation needs network access. The offline test
suite currently has 327 tests; synthetic grading fixtures verify harness behavior, not
agent quality. Validation checks structure and native metadata export, not cloud access.

Then choose your path:

| Goal | Start with |
| --- | --- |
| Compare this platform with another implementation | [Handoff guide](docs/AGENT_HANDOFF.md) and [architecture](docs/ARCHITECTURE.md) |
| Evaluate your own ADK agent and datasets | [Integration/configuration and EvalSet import](docs/REUSABLE_ADK.md) |
| Run the supplied examples with real Google grades | Cloud setup and reusable harness commands below |
| Evaluate an already deployed agent | [Hosted contract and configuration](docs/HOSTED_AND_REPORTS.md); the metadata extension is required |
| Inspect results or recover failed grading | [Reports](docs/HOSTED_AND_REPORTS.md#read-or-regenerate-a-report) and [recovery](docs/QUEUE_AND_RECOVERY.md) |
| Explore Google UI publication without changing the harness | [Measured UI research, retained native results and reproduction](docs/GOOGLE_UI_RESEARCH.md) |

Local execution of a Gemini agent still calls Google and is billable. The offline checks
above do not establish new live quality scores or hosted compatibility for your agent.

## Evidence and review status

Platform hardening and review results are in [HARDENING.md](docs/HARDENING.md)
and [RESULTS.md](docs/RESULTS.md). Source control and the source ZIP exclude generated
runs, reports and verification sidecars. Historical native evidence stays in a local
archive and can be supplied separately for replay. A fresh checkout runs all offline
contract tests using small synthetic fixtures, without that archive or cloud access.

One small, explicitly curated exception is the [Google UI research bundle](docs/GOOGLE_UI_RESEARCH.md):
two saved fictional support cases and sanitized native results from four cloud regrades.
It preserves the publication research without importing the historical run archive.
Existing-score publication to Experiments worked; richer Evaluation UI grade visibility
remained incomplete. This is an optional research probe, not built-in UI synchronization.

**Quota-aware grading and explicit recovery:** one-host shared dispatch budgets, immutable grading attempts, and recovery of unavailable grades without agent reruns. See [queue and recovery](docs/QUEUE_AND_RECOVERY.md), [review and load results](docs/REVIEW_AND_LOAD.md), and [all evaluation results](docs/RESULTS.md).

The two Gemini-backed examples cover fictional financial operations and support tickets.
A separate fail-closed policy checks coverage, exact tool/state contracts and release
thresholds. All example tool writes are offline simulations.

Start with [REUSABLE_ADK.md](docs/REUSABLE_ADK.md), [CALIBRATION.md](docs/CALIBRATION.md),
and [UPSTREAM_ISSUES.md](docs/UPSTREAM_ISSUES.md). No non-ADK adapter is included.

**Execution status and real scores:** [RESULTS.md](docs/RESULTS.md).
**Metric coverage:** [METRIC_INVENTORY.md](docs/METRIC_INVENTORY.md).
**Hosted ADK targets and readable evidence reports:** [HOSTED_AND_REPORTS.md](docs/HOSTED_AND_REPORTS.md).

## Cloud setup for live evaluations

Tested on macOS arm64 and a fresh Linux ARM64 Debian container under Docker Desktop.
The runner requires POSIX process groups; the legacy shell wrapper also requires Bash.
Native Windows is unsupported; the Linux evaluation client was verified on ARM64.
Hosted server architecture is recorded separately in deployment evidence.
Linux verification used existing Google credentials, not separate-account onboarding.

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Google Cloud CLI (`gcloud`) for ADC login, Google Cloud credentials,
a project with billing, Vertex AI/Gemini model access, and managed evaluation access.
The pinned client versions are Agents CLI 1.11.0, ADK 2.12.0 and aiplatform 2.2.0.

```bash
uv sync --locked --python 3.12
cp .env.example .env
# Edit GOOGLE_CLOUD_PROJECT in .env. Do not commit this file.
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

The principal needs permissions to invoke the model and evaluation service in the chosen
project. `roles/aiplatform.user` is a common starting role; your administrator should grant
only the permissions required in your environment, including service usage when needed.
Enable `aiplatform.googleapis.com` and billing if your administrator has not already done so.
The run refreshes ADC without displaying the token and performs real billable Google calls.
No bank, notification service, deployment, or Azure credentials are needed.

Use `GOOGLE_CLOUD_LOCATION=global`. The agent uses `gemini-3.8-flash` with bounded retries
for transient errors. If your project cannot access this model or the managed evaluator,
the run fails; it never substitutes mock scores. A Gemini API key alone is not the documented
configuration for this repository's managed grading path.

## Run either agent through the reusable harness

```bash
uv run python -m adk_eval_harness validate --config packs/support/config.json
uv run python -m adk_eval_harness run --config packs/finance/config.json --output artifacts/runs/finance-new
uv run python -m adk_eval_harness run --config packs/support/config.json --output artifacts/runs/support-new
```

Output directories must be new. Real ADK execution captures every turn in a persistent
session; native `agents-cli eval grade` supplies all scores. Exit 0 means the configured
policy passed, including complete, valid native results. Exit 1 means failed verification.
Setup/argument errors may exit 2. Each agent turn defaults to three minutes; the whole
generation budget scales with the number of turns. Native grading defaults to 30 minutes
and can be configured with `grading_timeout`. The complete two-agent verification makes many
managed judge calls and can take tens of minutes; service latency varies. No low-score
retry occurs. Schema-3 failed grading can be explicitly recovered from saved traces into a new output directory, without rerunning agents. See [recovery commands and limits](docs/QUEUE_AND_RECOVERY.md).

Policy v2 keeps full-conversation tool-use and trajectory scores as mandatory advisory
metrics with visible warnings. Task success and all other selected metrics remain blocking;
exact authorization/tool/state checks always block. Advisory service failures still block.
No thresholds are lowered. See the calibration report before interpreting a policy pass.
Never interpret the native CLI's exit 0 alone as a quality pass.

Every finalized run also writes `report.html` and `report.json` with prompts, replies,
tool events, native explanations, attempts and available agent telemetry. Regenerate an
existing report offline with:

```bash
uv run python -m adk_eval_harness report --output artifacts/runs/support-new
```

Missing token/timing fields are shown as unavailable. Agent usage and native judge usage
are separate; these observations are not billing totals. Reports verify saved evidence and
policy before displaying a verdict. The optional hosted adapter executes a compatible ADK
API server with a required deployment-metadata extension. Its private Cloud Run fixture is
a single-instance POC with in-memory sessions; see [scope, setup and report semantics](docs/HOSTED_AND_REPORTS.md).

The original finance-only runner remains for historical policy-v1 reproduction:

```bash
bash scripts/run_evals.sh --output artifacts/runs/legacy-policy-v1
```

The intentionally incorrect-reference suite must fail:

```bash
bash scripts/run_evals.sh --policy tests/eval/policies/negative.json
# Expected: exit 1, final_response_match score below 1.0.
```

This negative control is a real Google grade of a real agent response against a deliberately
wrong golden answer. It is not a fabricated evaluation result or a production dataset.

## Native commands

```bash
export CI=1 AGENTS_CLI_TELEMETRY=0
.venv/bin/agents-cli eval metric list
.venv/bin/agents-cli eval run --dataset tests/eval/datasets/smoke.json \
  --metrics final_response_match --output artifacts/runs/native-smoke

.venv/bin/agents-cli eval generate \
  --dataset tests/eval/datasets/grounding/cases.json \
  --output artifacts/runs/grounding-traces.json --concurrency 1
.venv/bin/agents-cli eval grade --traces artifacts/runs/grounding-traces.json \
  --config tests/eval/configs/grounding.yaml --output artifacts/runs/grounding-grades --qps 2

.venv/bin/agents-cli eval compare PATH_TO_BASELINE_RESULTS.json PATH_TO_CANDIDATE_RESULTS.json
```

These are installed, verified arguments. The legacy runner uses native `generate` for single-turn suites.
The reusable harness uses an ADK Runner for both single-turn and conversation suites.
Both use native `grade` to assign each
suite its own artifact paths. `eval run` is also demonstrated in the captured smoke evidence.
The CLI's `--output` for generation is a file path unless the directory already exists.

## Datasets, policies and tests

- `adk_eval_harness/`: domain-independent ADK execution, native grading and policy validation.
- `packs/`: per-agent configurations, policies and frozen prospective datasets.
- `examples/`: support agent and separate finance/support assertion modules.
- `agent/`: finance ADK agent, seven offline tools and fictional fixtures.
- `tests/eval/datasets/`: response, golden, tools, grounding, rubric, safety, failures,
  development regression, stateful conversation scripts, a frozen holdout and negative controls.
- `tests/eval/configs/`: native Google metric selection, with no custom quality evaluators.
- `tests/eval/policies/`: per-metric thresholds, exact case coverage and deterministic assertions.
- `adk_eval_harness/gate.py`: validates native results and traces; does not implement Google metrics.
- `scripts/quality_gate.py`: finance compatibility wrapper for historical evidence.
- `artifacts/`: ignored local runs, evidence, reports, archives and verification sidecars; none are required by the unit tests.

```bash
.venv/bin/python -m pytest -q
# Report on a run you have created or received separately:
.venv/bin/python -m adk_eval_harness report --output path/to/saved-run
```

Tests cover permission binding, replay denial, notification ordering and corrupted native-shaped
results. Their temporary synthetic fixtures are never presented as Google evaluation output.
A missing result, metric, case, evaluator or score is a failure, not a skip.

## Reproducibility limits

The lockfile fixes client dependencies. Hosted agent and evaluator behavior can still change;
repeat runs are not guaranteed to reproduce identical scores. Thresholds in this POC need
calibration on a larger corpus before production use. General safety judges cannot prove
business authorization; deterministic safeguards remain mandatory.

The supplied v2 architecture is retained in `docs/agent_eval_architecture_v2.docx`.
[ARCHITECTURE.md](docs/ARCHITECTURE.md) describes the implementation and
[RESEARCH.md](docs/RESEARCH.md) records source checks and operational findings.
Azure pipelines, production deployment orchestration, model-comparison infrastructure and real transactions are out of scope. The opt-in private Cloud Run fixture supports hosted-adapter verification; it is not a production hosting platform.

For new integrations, recover unavailable native grades from a finalized schema-3 run:

```bash
uv run python -m adk_eval_harness recover --config packs/support/config.json \
  --from-run artifacts/runs/FAILED_RUN --output artifacts/runs/RECOVERED_RUN
```

Recovery preserves valid scores (including low scores) and failed attempts, executes no
agent turns, and refuses incomplete generation. See [recovery limits](docs/QUEUE_AND_RECOVERY.md).

The **legacy finance runner** also supports trace reuse, with different semantics:

```bash
bash scripts/run_evals.sh --reuse-traces artifacts/runs/PREVIOUS_RUN \
  --output artifacts/runs/NEW_REGRADING_RUN
```

The runner verifies agent and dataset hashes before reuse. Changed or incomplete suites are regenerated; every suite is graded again with Google. A failed prior run remains failed;
a regrade creates a distinct run with its own native results. Do not repeatedly regrade low scores
until they pass: diagnose the response, trace, and metric applicability first.

To build a shareable archive from the explicit public allowlist:

```bash
.venv/bin/python scripts/package_reference.py
```

This excludes `.env`, the older validation project, virtual environments, local databases and
private run logs. The raw private originals remain on disk; exported evidence retains scores
and explanations, with local configuration metadata redacted.

Optional native workflows (not required by the release gate):

```bash
.venv/bin/agents-cli eval dataset synthesize --count 1 --max-turns 2 \
  --instruction "Explore fictional account questions; do not request real transfers." \
  --output artifacts/runs/synthesized.json
.venv/bin/agents-cli eval analyze --eval-result PATH_TO_MULTI_TURN_RESULTS.json \
  --metric multi_turn_task_success --top-k 2 --output artifacts/runs/analysis.json
```

Both are experimental. Review synthesized cases before adopting them. Analysis supports
`multi_turn_task_success` and `multi_turn_tool_use_quality`; it is most useful with enough
failing cases to cluster. These flags were verified against installed help. They are not
silently invoked by the gate.

## Hardening and controlled failures

See [HARDENING.md](docs/HARDENING.md) for the session-local ledger contract, negative
controls, persistence limits, and evidence requirements. [CODE_SIZE.md](docs/CODE_SIZE.md)
separates implementation code from tests and datasets. Licensed under [Apache-2.0](LICENSE).

```bash
bash scripts/run_evals.sh --fault-injection --output artifacts/runs/faults
# Expected release-gate exit: 1. Then independently verify the intended failures:
.venv/bin/python scripts/verify_faults.py artifacts/runs/faults
# Expected verifier exit: 0 only if every fault was detected and native grading completed.
```

The fault agent is scripted ADK fault injection, not Gemini-generated behavior. Production
release runs always use the normal Gemini agent. The daily transfer limit and changing
balances exist only in one active session. Multi-turn generation uses one live ADK Runner session per conversation and exports real events,
including state deltas, to Google’s native CLI grader. Separate conversations start isolated sessions.

## Historical stateful pass (policy v1)

`generate_conversations.py` preserves one actual ADK session across every turn of a case.
It exports native `EvalCase` records with actual messages, tool calls, results and state deltas;
all quality scores still come from `agents-cli eval grade`. Raw events and per-turn session
snapshots accompany the traces. Cases use separate fresh sessions. A failed turn retains
partial evidence and cannot produce a complete trace. There is a 180-second turn timeout
and a 30-minute conversation-suite process timeout.

Five development conversations cover context, recovery, post-transfer balances, cumulative
limits, rejected requests and cancellation. Four then-prospective holdout conversations use
different amounts, reverse direction, an exact cumulative boundary, replay without fresh
authorization, and missing-account recovery. Holdout inputs, policy and metric config were
hashed before live execution. The runner rejects changes to frozen expectations. We do not
tune the agent against these scores. Once published and inspected, these cases are regression
coverage; a future independent evaluation needs a newly frozen holdout.

Linux verification uses a clean Python 3.12 Debian container, extracts this public ZIP, installs
the lockfile from the network with no host package cache, runs tests, and performs fresh live
generation and native grading, including fault and wrong-reference controls. See
`docs/STATEFUL_PASS.md` for the command, provenance, results and remaining boundaries.


## Reproduce both ADK examples on Linux

```bash
mkdir -p artifacts/runs/reusable-linux
# Mount only the public ZIP, a new evidence output directory, and your existing ADC read-only.
docker run --rm --env-file .env \
  -e GOOGLE_APPLICATION_CREDENTIALS=/run/private/adc.json \
  -v "$HOME/.config/gcloud/application_default_credentials.json:/run/private/adc.json:ro" \
  -v "$PWD/artifacts/google-agent-evals-demo.zip:/input/reference.zip:ro" \
  -v "$PWD/artifacts/runs/reusable-linux:/verification" \
  python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 \
  sh -c 'python -m zipfile -e /input/reference.zip /work && python /work/google-agent-evals-demo/scripts/linux_verify_reusable.py'
```

This installs the locked dependencies, runs tests, evaluates both agents and executed
negative controls, and produces calibration.json. A valid negative result must identify
each intentionally faulty case; a crashed run never counts as detected bad behavior.
Four cases (two finance and two support) were prospectively frozen before their original
evaluation. They and the four older finance holdouts are now published regression coverage,
not unseen holdouts. Both policies and all selected datasets were frozen before that run. Existing ADC reuse does not prove new-account onboarding.

## Concurrent evaluation

Set `case_concurrency` to run independent local or hosted cases concurrently (default 1).
Local concurrency requires a fresh agent factory. Turns remain ordered; generation failures
stop dispatch and retain partial evidence. Google grading and grader-only recovery are
unchanged. See [configuration, failure semantics and load verification](docs/CONCURRENCY.md).
