# Hosted ADK execution and evidence reports

The harness can execute a compatible Python ADK agent locally or through an authenticated
ADK API server. Both paths capture actual ADK events and use Google's native
`agents-cli eval grade` metrics. The hosted adapter changes where the agent runs; it does
not replace Google's graders or turn the example agent into the evaluation platform.

The included private Cloud Run deployment is a bounded POC. Its transport probe has
verified denied anonymous access, four concurrent isolated conversations with three turns
each, and no repeated agent POST after an ambiguous timeout. A Linux authentication smoke
used an existing gcloud user credential. Fresh Linux-package hosted evaluation completed for 12 load cases and 8 support cases,
with grading-only recovery proven against captured remote traces. Results are recorded
in [RESULTS.md](RESULTS.md); these checks do not establish
agent quality, general deployment compatibility, or production readiness.

## Configure a hosted target

Keep the same datasets, policy, native metric YAML and local agent import as a local pack.
Add this object to a copy of its config, and set `app_name` to the server's application name:

```json
{
  "hosted": {
    "url": "https://YOUR_PRIVATE_SERVICE.run.app",
    "auth": "gcloud",
    "deployment_manifest": "deployment-manifest.json"
  }
}
```

This is a fragment, not a complete pack. `hosted` and `runner_factory` are mutually
exclusive. The local agent import/factory supplies metadata for native trace export and
comparison; the remote server executes the agent turns. Imports and factories remain
trusted local code and may themselves have side effects. Matching local source, tools
and pinned dependencies must be available for metadata conversion.

The server must implement ADK 2.12 session create/get and `/run_sse`, plus the harness's
`GET /eval/metadata` extension. An arbitrary unmodified ADK endpoint is not sufficient.
The extension returns native agent metadata, source hashes and deployment revision;
`deployment/hosted/server.py` is the example. The adapter compares these values with a
reviewed expected deployment manifest, checks local agent metadata against that manifest,
and rejects metadata drift during a conversation. This is a server assertion checked
against an expected build, not independent remote attestation.

The development deployment helper stages an explicit source allowlist and produces a
command plan before execution:

```bash
uv run python scripts/deploy_hosted.py --project YOUR_PROJECT_ID \
  --region us-central1 --name YOUR_DEV_SERVICE --output artifacts/runs/deploy-plan
# Review plan.json, then use a new output directory for the actual deployment:
uv run python scripts/deploy_hosted.py --project YOUR_PROJECT_ID \
  --region us-central1 --name YOUR_DEV_SERVICE --output artifacts/runs/deployed --execute
```

The helper does not automatically remove the service, images, build bucket or service
accounts. The service may scale to zero, but cloud resources remain until explicitly
removed. Review and manage their lifecycle separately.

After deployment, create the expected manifest from the matching reviewed checkout and
its `endpoint.json` (including the ready revision). Keep the sibling `source/` staging
directory: the helper compares its file set and bytes with the current checkout offline.
A new source change requires a new deployment and expected manifest:

```bash
uv run python scripts/hosted_manifest.py \
  --endpoint artifacts/runs/deployed/endpoint.json \
  --output /path/to/your-pack/deployment-manifest.json
uv run python -m adk_eval_harness validate --config /path/to/your-pack/config.json
uv run python -m adk_eval_harness run --config /path/to/your-pack/config.json \
  --output artifacts/runs/hosted-new
```

Offline preflight checks pack structure and local metadata export; a successful preflight
is not a remote connectivity or IAM check. The run checks remote metadata before execution.
Do not make an unexpected server response into the new expected manifest merely to bypass
a mismatch: reconcile the deployed revision and reviewed source first.

Remote URLs require HTTPS and `auth: "gcloud"`. The caller needs Cloud Run invocation
access; the adapter privately obtains a user identity token with
`gcloud auth print-identity-token`. Judge calls still use the evaluation environment's
ADC. Service-account ADC, impersonation and workload identity as hosted transport
authentication have not been established by this implementation. `auth: "none"` and HTTP
are allowed only for loopback testing. Tokens and authorization headers are not evidence.

Each case receives a unique remote user and one session, shared across that case's turns.
State snapshots come from the server's session service. The client does not reconstruct
remote state from event deltas. Redirects and automatic HTTP retries are disabled. A
failed or timed-out session/run POST can have an unknown remote outcome: it fails the
case and is never automatically repeated.

The deployment uses one worker, at most one Cloud Run instance and in-memory sessions.
Restarts, revision changes and scale-to-zero can lose sessions. Multiple instances,
persistent session stores, durable tool-side effects, broader IAM onboarding and production
operation need separate implementation and verification. Agent generation supports
bounded `case_concurrency` (default 1); see [CONCURRENCY.md](CONCURRENCY.md). The shared
grading queue coordinates native judge dispatch on one host, not agent execution across a fleet.

## Read or regenerate a report

Completed run/recovery finalization writes `report.html` and `report.json`, including
failed evaluations. To regenerate them from an existing run:

```bash
uv run python -m adk_eval_harness report --output artifacts/runs/hosted-new
```

Open `report.html` locally. It is a self-contained HTML file with no external assets;
raw artifact links are relative, so retain the run directory when sharing it. The report
shows the execution target, verdict, each prompt and turn reply, tool calls/results,
native scores and explanations/rubric payloads, policy roles/thresholds, grading attempts
and remaining errors. Partial generation is explicitly incomplete and ungraded; emitted
partial events are visible without inventing a final answer or native score.

Report generation makes no agent or judge calls. It replays evidence integrity and the
saved deterministic policy using currently trusted assertion hooks. It validates policy,
trace/result hashes and recovery lineage rather than trusting an old `gate.json` verdict;
the recorded gate remains visible separately. This is historical evidence inspection.
Use `check --config CONFIG --output RUN` to also compare current source/config identity.
The `report` command's successful exit means files were written, not that the evaluation
passed; inspect the verdict or use `check` for a gate exit status.

Evidence persists as files in the output run directory on the filesystem. There is no
central report database, hosted report dashboard or automatic retention/deletion policy.
Keep or archive the complete directory, including linked artifacts and recovery history.
This evidence storage is separate from the hosted agent's in-memory session service.

Typical run files are:

| File | Purpose |
| --- | --- |
| `manifest.json`, `policy.json`, `gate.json` | Run identity, hashes, execution target, saved policy and recorded verdict |
| `deployment.json` | Expected hosted deployment manifest, when hosted execution is configured |
| `report.json`, `report.html` | Derived machine-readable and readable evidence views |
| `SUITE/traces.json` | Completed native trace records used for grading |
| `SUITE/sessions/CASE/turn_N.json` | Original user message, raw ADK events, measured telemetry and optional transport counters; turns are zero-indexed |
| `SUITE/sessions/CASE/state_N.json` | Session state fetched after a successful turn |
| `SUITE/sessions/CASE/runner_metadata.json` | Observed runner/deployment metadata when the adapter supplies it |
| `SUITE/completed_cases.jsonl`, `SUITE/progress.json` | Generation checkpoints; forensic evidence, not resumable agent execution |
| `SUITE/attempts/...` | Per-metric grading batch traces, metric config, command logs and unmodified native results; exact paths are in the manifest |
| `history/...` | Retained parent manifest/gate snapshots after explicit recovery |

Prompts, tool arguments/results and session state can contain sensitive application data.
The export helper preserves native and session artifacts byte-for-byte, refusing known
private identifiers in immutable evidence instead of rewriting the evaluated behavior.
It redacts supported manifest/log metadata and regenerates derived reports against the
export. This is not a general personal-data detector; review your datasets and evidence
before sharing them.

## What token and latency numbers mean

Agent tokens are observed values from raw ADK event `usage_metadata`, separate from
judge usage. The report sums reported prompt, candidate, thought, cached-content and total
token fields on nonpartial events, deduplicating event/invocation IDs. Partial streaming
usage is excluded and marked uncertain. Missing fields are unavailable, never zero.
Categories can overlap: do not add them together or interpret the result as an invoice.
Not every ADK agent/model emits usage, and scripted agents can have no token telemetry.
Native judge token counts remain unavailable unless the result explicitly supplies them;
they are not estimated from case counts or agent tokens.

Turn duration uses the client's monotonic clock around execution and the subsequent
session-state fetch/save. For hosted runs it includes network/authentication and metadata
checks in that interval. It excludes initial session creation, raw turn-file serialization
and runner closure; it is not isolated model inference latency. First-event and first-text
timings are separately measured; first text excludes thought/user/tool-response parts and
can include partial assistant text. The report gives observed coverage, mean and p50/p95
turn durations. Command durations and queue wait are separate measured observations,
not a reconstructed end-to-end or per-judge latency.

Current runs bind raw session JSON files through `manifest.telemetry_sha256`. Changed or
missing bound evidence makes the report incomplete and excludes affected usage/timings.
Older runs can still be reported but are labelled **unbound historical telemetry**.
Missing historical timings are not inferred from ADK event timestamps, and unavailable
old token fields stay unavailable.

## Recover grading without repeating agent work

After complete generation, unavailable/error grading pairs can be explicitly recovered:

```bash
uv run python -m adk_eval_harness recover --config /path/to/your-pack/config.json \
  --from-run artifacts/runs/hosted-new --output artifacts/runs/hosted-recovered
```

Recovery reuses the exact captured traces and telemetry; it does not import/run the agent
or contact its hosted endpoint. Valid native grades, including zero, are retained and never
retried. Prior errors and attempt history remain visible. Incomplete agent generation is
not recoverable by this command. See [queue and recovery](QUEUE_AND_RECOVERY.md) for
attempt limits, source identity checks and queue filesystem requirements.
