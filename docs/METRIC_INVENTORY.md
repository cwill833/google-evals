# Built-in metric inventory

Verified 2026-10-21 against `google-agents-cli==1.11.0` and `google-cloud-aiplatform==2.2.0`.
The captured command output is metric-list.txt (local `artifacts/evidence/research/metric-list.txt`).
There are **17** advertised metrics. The CLI lists names, not descriptions or input requirements.
The table combines installed Google SDK types and Google's versioned bundled evaluation references.
Live verification status is maintained separately in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## Shared contracts

An input file is `{"eval_cases": [...]}`. Every case has a unique `eval_case_id`.
Single-turn inputs use `prompt: {role: "user", parts: [{text: "..."}]}`.
Native generation adds `responses: [{response: {role: "model", parts: [...]}}]`
and `agent_data: {agents: {...}, turns: [...]}`. Each turn has a sequential
`turn_index` and ordered `events`. Tool calls and observations are structured
`function_call` and `function_response` parts, not prose summaries.

`reference` has the **ResponseCandidate** wrapper: `{response: {role: "model", parts: [...]}}`.
`context` is source text or a Content object. Neither is invented by the generator.
`rubric_groups` is a map of names to `{rubrics: [{rubric_id, content: {property: {description}}}]}`.
One rubric group needs no selector. Multiple groups require
`metric_spec_parameters.rubric_group_key` in a managed metric configuration.

All selected built-ins return native per-case metric results with `metric_name`,
`score`, `explanation`, `rubric_verdicts`, `raw_output`, and `error_message` (some nullable).
Rubric-based scores are fractions passed in [0,1], higher is better. A null score
or an error is never a failing-quality score: it is an unavailable evaluation and
blocks the release. Static metrics may provide explanations without rubric verdicts.
The service appends versions (`_v1`, or `_v2` for final response match).

## Metric matrix

| Metric | Purpose and required inputs beyond common fields | Applicable trace | Outputs and limitations |
| --- | --- | --- | --- |
| `general_quality` | Prompt and response; automatically generated content criteria | Single turn | Fraction satisfied plus rubric reasoning; broad criteria may assume a general assistant and miss application-specific intent |
| `text_quality` | Prompt and response; fluency, coherence and language | Single turn | Fraction satisfied and rubric reasoning; does not establish financial correctness |
| `instruction_following` | Prompt constraints and response; optional authored rubric group | Single turn | Fraction satisfied and rubric reasoning; not an authorization enforcement mechanism |
| `final_response_quality` | Prompt, response and full intermediate tool trace | Single turn | Adaptive rubric scores covering answer and tool context |
| `final_response_match` | Response plus wrapped `reference` | Single turn | Semantic match score and explanation, not byte/string equality; intentionally incorrect reference demonstrates failure |
| `final_response_reference_free` | Response and explicit `rubric_groups` | Single turn | Fraction of authored rubrics passed; bundled Google guidance reports server errors without rubrics |
| `tool_use_quality` | Tool declarations, structured function calls, arguments and tool observations | Single turn, including multi-step workflows | Adaptive tool-selection/argument/order assessment; not a deterministic sequence assertion |
| `grounding` | Response and explicit source `context` | Single turn | Sentence support/contradiction assessment; missing context can silently collapse scores to zero |
| `hallucination` | Response claims and actual tool-returned context | Single turn with tool observations | Claim-grounding score; does not establish truth beyond supplied fixture evidence; higher means less hallucination |
| `safety` | Prompt and response | Single turn | Fixed safety-policy evaluation; covers general harmful-content categories, not application-specific approval rules |
| `multi_turn_task_success` | Full `agent_data.agents` and ordered conversation turns | Actual multi-turn agent trace | Adaptive goal-completion rubrics; Google's guidance says supplied rubric groups are ignored |
| `multi_turn_tool_use_quality` | Full conversation including tool declarations, calls and observations | Actual multi-turn agent trace | Adaptive technical and semantic tool-use assessment |
| `multi_turn_trajectory_quality` | Full chronological conversation and intermediate events | Actual multi-turn agent trace | Adaptive efficiency, sequencing and recovery assessment |
| `multi_turn_general_quality` | `conversation_history: list[Message]`, current `prompt`, final `responses` | Chat-history representation, not native agent_data turns | Native agent traces returned HTTP 400; a text-only projection of real messages into chat history succeeded (mean 0.7321). Not interchangeable with task success |
| `multi_turn_text_quality` | Same chat-history representation | Chat-history representation, not native agent_data turns | Native agent traces returned HTTP 400; the real-message chat projection succeeded (mean 0.7917). Tool observations are omitted, so this is not a tool assessment |
| `gecko_text2image` | Text prompt and generated image content | Multimodal image generation | Listed by installed SDK but outside this text/tool agent; no applicable image output, not run |
| `gecko_text2video` | Text prompt and generated video content | Multimodal video generation | Listed by installed SDK but outside this text/tool agent; no applicable video output, not run |

The two Gecko metrics' detailed score scales and media restrictions were **not live verified**;
we do not assign them thresholds or claim coverage. SDK properties for other historical metric
names are not evidence of current supported built-ins: selection is limited to the native list.

## Compatibility and operational findings

- A multi-step single-turn tool workflow remains one conversation turn. It is not evidence for multi-turn coverage.
- Native SSE generation seeds earlier history and executes only the final user message. Our orchestrator chains actual generated traces, with every intermediate input/output preserved, to execute all turns.
- Single-turn metrics reject multi-turn `agent_data`. Separate datasets/configs prevent accidental cross-application.
- Native generation can return exit 0 with only successful cases after partial failure. Required-case coverage must be checked independently.
- Native grading excludes error cases from averages and can return exit 0 with evaluator errors or low scores. Per-case results and error counts are authoritative.
- Callback state-only events remain in raw traces. CLI 1.11.0 drops these in-memory for grading because the remote service requires event content. The gate accounts for only this documented transformation.
- `CI=1` disables the CLI's optional npx skill freshness check; otherwise restricted environments can see a 15-second warning unrelated to metric availability.
- The managed evaluator backend is not frozen by `uv.lock`. Judge behavior, availability, and latency can change despite pinned client dependencies.

## Primary sources

- [Google evaluation guide](https://google.github.io/agents-cli/guide/evaluation/)
- [Google CLI reference](https://google.github.io/agents-cli/cli/)
- [Version 1.11.0 release](https://github.com/google/agents-cli/releases/tag/v1.11.0), including the installed `google/agents/cli/skills/data/google-agents-cli-eval/references/metrics-guide.md` and `dataset_schema.md`
- [SDK eval types](https://github.com/googleapis/python-aiplatform/blob/main/agentplatform/_genai/types/evals.py)
- [ADK evaluation overview](https://adk.dev/evaluate/) — ADK's own `adk eval` metrics are a separate surface from Agents CLI's managed metric catalog.

## Live compatibility probes

All 13 requested metrics produced valid native Google results. The two additional chat-history
metrics also produced valid results, for **15 of 17** advertised built-ins exercised successfully.
The Gecko media metrics remain inapplicable to the agent's text/tool output.

`artifacts/evidence/extra-metrics/` preserves both the failed direct-agent-data attempt and
the successful chat-history projection. The projection copies actual user/agent text from
the three-turn traces into `conversation_history`, `prompt`, and `responses`; it does not
generate replies or implement a judge. It removes tool observations, so these two extra
metrics are research coverage and are not in the tool-agent release policy. Their lower scores
are retained rather than hidden or counted as release passes.

In this environment, supplying the clarification case's authored rubrics to `text_quality`
and `instruction_following` caused repeated HTTP 400 rubric-computation errors. The final
configuration uses `final_response_reference_free` for that authored clarification rubric.
The three general response metrics grade the bare-JSON case with auto-generated rubrics.
General instruction-following is not used on the malicious theft request; observed generated
criteria incorrectly demanded theft instructions. Safety and deterministic no-write assertions
cover that request. These are metric-fit corrections, not threshold reductions.
