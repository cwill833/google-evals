# Upstream investigation — 2026-10-21

Checked Google's public Agents CLI open-issue list (20 issues, excluding PRs),
ADK Python open issues matching evaluation (17 results), Vertex AI SDK open evaluation
issues (18 results), installed CLI 1.11.0 evaluation
skill, and Google's public skills catalog. Search absence does not prove no report exists;
this is a dated, bounded search, not an exhaustive search of internal Google trackers.
Follow-up public searches for open issues naming multi_turn_tool_use_quality and for
open rubric issues in python-aiplatform returned zero matches; that is not proof that
no related closed, differently worded or internal report exists.

| Source | Finding | Relevance / action |
| --- | --- | --- |
| [CLI evaluation skill](https://github.com/google/agents-cli/blob/main/skills/google-agents-cli-eval/SKILL.md) | Ordinary SSE generation seeds history and answers the trailing user turn; Live generation differs. A successful eval command is not a passing quality gate. | Keep real persistent ADK Runner conversations and inspect native scores explicitly. Use the installed 1.11.0 skill for pinned behavior. |
| [ADK #4532 — evaluator session state](https://github.com/google/adk-python/issues/4532) | Open proposal reports state is absent from the local evaluator interface. | Related architectural gap, not proof of a managed rubric bug. Retain actual session snapshots and deterministic state checks. |
| [ADK #7379 — callback replies](https://github.com/google/adk-python/issues/7379) | Open report describes rubric errors when before_agent_callback supplies the answer without a model request. | Different local ADK metric/path from our managed CLI metrics. Our exporter captures agent metadata directly, but do not claim this issue is fixed upstream. |
| [ADK #7649 — default judge models](https://github.com/google/adk-python/issues/7649) | Open report flags older default judge/simulator models and new-account access. | Our agents explicitly select their model. Hosted native graders choose their own models. No evidence this caused the four finite, successful grades we disputed. |
| [CLI #25 — existing project initialization](https://github.com/google/agents-cli/issues/25) | Open feature request for a lightweight manifest initializer. | Document the minimal project manifest for adopting the harness in an existing ADK checkout. |
| [CLI #54 — experiment visibility](https://github.com/google/agents-cli/issues/54) | Open question about submitted evaluations appearing in Cloud experiments. | Separate from grading correctness; local native result artifacts remain authoritative here. |
| [CLI #49 — evaluation quality contract](https://github.com/google/agents-cli/issues/49) | Open community proposal for machine-readable evaluation completeness evidence. | Proposal, not a shipped fix. Our manifest/coverage gate addresses this locally. |

Additional SDK breadcrumbs (all open at inspection; reports cover older client versions,
so an open issue is not proof that the pinned version still has every reported defect):

- [Vertex SDK #6317](https://github.com/googleapis/python-aiplatform/issues/6317):
  multi_turn_general_quality fails for missing conversation_history even when supplied.
  A commenter suspects the DataFrame conversion drops it. This matches our earlier
  compatibility symptom; it does not explain finite low scores from the three supported
  multi-turn agent metrics. Keep the incompatible metric out of agent-trace suites.
- [Vertex SDK #6785](https://github.com/googleapis/python-aiplatform/issues/6785):
  malformed ConversationTurn objects and absent turn_index in simulated inference.
  The reporter offers a schema-loosening patch. Our adapter instead builds typed turns
  explicitly, preserves raw events, and requires contiguous indexes; no SDK monkeypatch.
- [Vertex SDK #6976](https://github.com/googleapis/python-aiplatform/issues/6976):
  local inference drops non-text prompt parts. The reported alternative is ADK's own
  evaluation route. This reference explicitly rejects non-text inputs instead of silently
  flattening them; multimodal support is outside the verified contract.
- [Vertex SDK #6111](https://github.com/googleapis/python-aiplatform/issues/6111):
  AgentInfo conversion assumes instruction exists on a SequentialAgent. This is another
  reason to verify workflow/multi-agent support separately before claiming all ADK shapes.

Google does publish a [platform troubleshooting skill](https://github.com/google/skills/blob/main/skills/cloud/agent-platform-troubleshooting/SKILL.md)
with a [known-issues catalog](https://github.com/google/skills/blob/main/skills/cloud/agent-platform-troubleshooting/references/known-issues.md).
Its listed failures concern runtime, gateways, IAM, networking and deployment, rather than
managed evaluation rubric applicability. No matching workaround was found there.

The more relevant [evaluation flywheel failure patterns](https://github.com/google/skills/blob/main/skills/cloud/agent-platform-eval-flywheel/references/failure_patterns.md)
recommend inspecting rubric verdicts against traces and distinguishing infrastructure
failures from quality issues. They offer agent/tool prompt improvements and timeout
mitigations, but no documented override for the contradictory adaptive multi-turn rubrics
observed here. The newer SDK skill and installed CLI guide also differ on some field and
metric support details; runtime schemas and pinned CLI behavior take precedence for this package.

Conclusion: no verified upstream issue/fix was found for our exact refusal/cancellation
rubric disagreements. Treat this as an observed hosted-evaluator limitation, not a
Google-confirmed bug ID. Preserve traces/rubrics for a minimal upstream report. No issue
or comment was posted. The advisory policy is an explicit local mitigation, not an
upstream workaround advertised as a fix. Dependency pins do not freeze hosted judges.

Observed in the current CLI logs: grade drops content-less callback events because the
service rejects them (16 events in the finance development conversation suite). Our
source traces retain them, and the deterministic gate checks their state deltas. This
is a grading-input limitation, but we have not established it as the cause of the
wrong-turn rubric judgments. Instructions, user turns and tool responses remain present.

## Load-pass findings (2026-10-21)

A real 200-case native `final_response_match` load at qps=2 produced 181 valid
scores and 19 judge-output parsing errors. The SDK logged 85 bounded 429 retries.
All 200 actual ADK outputs exactly equaled their references, but 34 valid native
scores were zero. Saved explanations identified the fixture's repeated `x` payload
as masked/redacted content and declined to compare it. Twelve errors reported a
missing label; seven reported an unknown label such as `cannot_be_determined`.
The harness correctly rejected the run as incomplete. This is not evidence of
19 incorrect agent answers or a demonstrated 200-case harness ceiling.

The repeated placeholder payload was unsuitable for interpreting a semantic
match judge. It was changed to ordinary inventory text with an explicit task;
the original outputs, native grades and failure remain preserved. A lower dispatch
rate is a separate mitigation for service pressure, not an asserted fix for judge
parsing. No score was rewritten and no upstream evaluator was patched.

The local SDK catalog lists `final_response_match_v3`, but the CLI's ordinary
metrics branch passes bare names unchanged. The pinned SDK resolves its
`RubricMetric.FINAL_RESPONSE_MATCH` property to v2. Therefore catalog discovery
cannot promise the execution version. The harness now reports catalog candidates
separately and records the actual native metric name from results; explicit version
pins must match exactly. No official source was found establishing that v3 fixes
these parsing errors, and no automatic version substitution is performed.

Google documents that evaluation metrics can consume multiple underlying judge
requests and shared capacity. Lower/smoother dispatch and bounded backoff can help
429s; they cannot guarantee capacity. See [evaluation quotas](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/quotas)
and [Google 429 guidance](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/deploy/error-code-429).
