# Tooling verification

For the later **2026-10-24** experiment publishing saved runs to Google's UI, see
[Google UI research](GOOGLE_UI_RESEARCH.md). It includes sanitized live results,
failed/blocked approaches and reproduction commands; it does not change the harness.

Checked on 2026-10-21. The supplied architecture v2 is a proposal, not execution evidence.
Its v1.10.0 recommendation is superseded by a fresh package and release check:

- GitHub latest release: v1.11.0, published 2026-10-20T13:03:57Z.
- PyPI distribution: `google-agents-cli`, version 1.11.0; command: `agents-cli`.
- Python requirement >=3.11; this project uses Python 3.12 and restricts Python to <3.14.
- CLI requirement: `google-cloud-aiplatform[evaluation]>=2.2.0,<3`.
- New-project release-note recommendation: ADK >=2.12.0.
- Installed pins: CLI 1.11.0, ADK 2.12.0, aiplatform evaluation SDK 2.2.0.
- `uv.lock` locks all transitive dependencies and includes hashes. `uv` used here: 0.10.0.
- Google ADC refresh and live Gemini/managed evaluation access succeeded. Tokens were never printed or stored in artifacts.

Installation uses `uv sync --locked`, the virtualenv equivalent of Google's documented
pip installation. Installing coding-assistant skills globally is not needed for evaluation.
Telemetry is disabled. The CLI's optional skill check is disabled via its supported CI detection.

Current release notes introduce recipe discovery changes and newer SDK requirements; no
recipe scaffold, deployment, identity migration, or cloud infrastructure is needed here.
The native manifest identifies the local `agent` directory. Default paths under `tests/eval`
follow Google's Python convention.

The existing workspace's `validation/` directory and older design documents are retained.
They are not evidence for this implementation. Root `.env` is ignored, and no existing
credentials were changed. The evaluation run uses the locally configured Cloud project.

## Known issues investigated

The [upstream issues list](https://github.com/google/agents-cli/issues) was checked.
[Issue 96](https://github.com/google/agents-cli/issues/96) describes global skill discovery;
this is not a managed-evaluation blocker in CI. Cloud deployment and recipe issues are out of scope.
More relevant behavior was verified directly in the installed code and live artifacts:
partial-case success can exit zero, grade errors are excluded from averages, single-turn
metrics reject multi-turn agent traces, and content-less callback events are removed before grading.
See the metric inventory and results for actual probes.

`EvaluationDataset.model_json_schema()` fails because the SDK type also accepts a pandas
DataFrame. Datasets are instead validated with the SDK's `model_validate` API, the same
validation used by the native CLI. No invented JSON schema is substituted.

## Official references consulted

- https://github.com/google/agents-cli
- https://google.github.io/agents-cli/
- https://google.github.io/agents-cli/guide/getting-started/
- https://google.github.io/agents-cli/guide/evaluation/
- https://google.github.io/agents-cli/cli/
- https://github.com/google/agents-cli/blob/main/RELEASE_NOTES.md
- https://github.com/google/agents-cli/releases/tag/v1.11.0
- https://pypi.org/pypi/google-agents-cli/json
- https://google.github.io/adk-docs/evaluate/ (redirects to https://adk.dev/evaluate/)

Installed `--help` outputs and `metric list` output are captured in `artifacts/evidence/research/`.

## Findings from the implementation loop

1. **Real 429 responses:** the first full run failed during tool inference. The agent now uses
   Google's `Gemini(..., retry_options=HttpRetryOptions(attempts=5, initial_delay=5, max_delay=30))`
   and the runner dispatches inference cases sequentially. Exhausted retries still fail the gate.
2. **Unreliable managed rubric computation:** text-quality returned HTTP 400 with
   `Rubric results could not be reliably computed.` The CLI saved a null/error result and exited 0.
   Its displayed mean used only the other case. A separately preserved regrade completed;
   it did not erase the original error or make the original run pass.
3. **Metric applicability matters:** generic instruction-following rubrics penalized a refusal
   for not providing theft instructions. Adversarial requests now use `safety` plus mandatory
   deterministic no-write assertions. Instruction-following remains exercised on legitimate prompts.
4. **Domain-specific rubric context:** a generic transfer clarification prompted changing judge
   criteria about real payment methods. The fictional sandbox case now explicitly declares its
   domain and supplies managed rubrics for missing details, simulation, authorization and readability.
   No threshold was lowered. Original judge results remain preserved.
5. **Judges do not replace parsers:** a JSON-only response included Markdown fences; a broad judge
   accepted it, while `json.loads` in the gate correctly rejected it. The agent instruction now
   explicitly requires bare JSON for JSON-only requests.
6. **ADK 2.12 restoration constraint:** `_validate_session_initialization_events` rejects non-default
   `EventActions`. Agents CLI translates `state_delta` into those actions when seeding history.
   Replaying raw callback-state events therefore returned HTTP 400. Continuation input now keeps
   genuine content-bearing events and strips client-supplied state deltas. Original `turn_N.json`
   files retain untouched native output. This is a compatibility adapter for inputs, not a custom
   evaluator. Permissions are always re-derived from the new user message, never restored.

7. **Partial-success generation confirmed live:** one run saved only one of two grounding cases
   and returned exit 0. The gate rejected the missing case. The runner now checks case coverage
   and deterministic trace assertions before grading and before reusing prior traces; incomplete
   prior suites are regenerated. A regression test uses that actual partial trace.
8. **Three multi-turn metrics:** two actual three-turn conversations each received 1.0 for
   task success, tool-use quality, and trajectory quality. The two extra chat-history metrics
   rejected native agent_data (HTTP 400), but graded a real-message chat projection successfully.
9. **Low score is not a native process failure:** the intentional wrong-reference case received
   final_response_match 0.0 with native exit 0. Our unchanged threshold of 1.0 returned exit 1.


## Fresh-package hardening follow-up

See RESULTS.md and HARDENING.md for the three clean-package extractions and final full transport-failure retry, the missing USD
instruction/context defects, native grounding “No sentences found” anomaly, and trajectory
false positives from inapplicable conditions. A managed rubric-group probe returned generated
IDs instead of the supplied IDs; no custom judge or score rewrite was adopted. The official
[metric reference](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/rubric-metric-details)
describes trajectory grading as adaptive rubric generation. The final run retains its native
scores and strict thresholds. All controlled negative cases execute actual offline ADK tools.
