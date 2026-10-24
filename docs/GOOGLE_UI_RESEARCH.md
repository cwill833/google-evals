# Google UI publication: measured results and reproduction

Observed **2026-10-24**, using Agents CLI 1.11.0, ADK 2.12.0 and
google-cloud-aiplatform 2.2.0 from the locked environment. This is a research probe,
not an integrated harness feature. It does not change execution, grading or gates.

**Publishing existing native scores to Experiments worked. Cloud regrading also
worked, but the newer Evaluation UI did not display the resulting grades.** An API
run in `SUCCEEDED` state is not proof that the UI displays its scores.

## What was actually tested

Two previously captured hosted support cases (`read_T101`, `read_T202`) were reused.
Each contains a real saved ADK tool call and final response about a fictional ticket.
No ADK agent was invoked or redeployed during these probes. Four cloud runs produced
eight new `final_response_match_v2` grades, all 1.0. The two original grades were
separately logged unchanged to Experiments. These small positive cases test integration,
not calibration, agent quality, failure rendering, scale or multi-turn UI support.

| Route | API result | Observed Google UI result |
| --- | --- | --- |
| Existing scores → Experiments (`log_metrics`) | Original case/aggregate values read back; no regrading | Both case scores, mean, pass rate, valid/error counts visible; source hash in parameters |
| `agents-cli eval submit`, global | Two cases, mean 1.0; SDK automatically created an experiment | Experiment absent from Evaluation list during observation |
| Explicit SDK experiment, global | Two cases, mean 1.0 | Direct experiment URL opened the dataset; no grades; absent from list |
| Explicit SDK experiment, us-central1 | Two cases, mean 1.0 | Listed under Models → Evaluation; detail showed prompts/golden responses, no grades |
| Same regional set + explicit candidate configuration | Two cases, mean 1.0; saved requests identical to preceding run | No improvement in grade visibility |
| Browser CSV upload | No run submitted | Blocked by the browser extension's disabled file-URL upload permission |

Agents → Evaluation showed no proof experiment rows. The successful score view was
Models → More services → **Experiments**, a different UI from **Evaluation**.
It is a generic metrics/parameters view, not an automatic import of the harness's
full prompts, tool traces, failure history and report. Account-bearing screenshots
were inspected and retained locally; this public bundle does not contain them.

## Retained evidence

The deliberately small [research bundle](research/google-ui-2026-10-24/evidence.json)
contains all four run readbacks, timestamps, native summary statistics, eight case
scores and their unmodified explanations, experiment metadata, original-score API
readback, the failed metric-name attempt and UI observations.

- [Saved traces](research/google-ui-2026-10-24/saved-traces.json): exact bytes submitted,
  including prompts, golden/actual responses, tool definitions and historical events.
- [Original native grade excerpt](research/google-ui-2026-10-24/original-native-grades.json):
  original results/summary fields; the repeated embedded dataset is omitted.
- [Reproduction helper](../scripts/probe_google_ui.py): parameterized version of the
  experiment, publication and readback calls. This helper was checked offline; the
  historical live results came from the original isolated scripts, not a new rerun
  of this generalized helper.

Project ID/number, bucket names and private source paths are replaced with explicit
placeholders in metadata. Run IDs remain for correlation with privately held originals.
Scores, judge explanations and timestamps are not rewritten. Source and excerpt SHA-256
values are recorded separately: a hash identifies bytes, not independent attestation
that a service generated them. This bundle is historical live evidence, **not synthetic
unit-test output** and not a full replayable harness run directory. The larger archive
remains excluded from the PR and ZIP. No credentials or account screenshots are included.

## Reproduce without executing an agent

Use the locked environment and your own Google project, ADC and existing private GCS
bucket. The principal and evaluation service need the appropriate evaluation/storage
permissions. No agent deployment is required. Regrading is billable; storage may also
incur charges. Commands create resources and do not delete them. Choose fresh experiment,
output and destination names for each attempt; do not overwrite earlier failures.

```bash
uv sync --locked --python 3.12
export CI=1 AGENTS_CLI_TELEMETRY=0
export PROOF_PROJECT='YOUR_PROJECT_ID'
export PROOF_BUCKET='YOUR_EXISTING_PRIVATE_BUCKET'
export PROOF_TAG='choose-a-unique-lowercase-name'
```

Publish the original scores without a judge call:

```bash
uv run python scripts/probe_google_ui.py publish \
  --project "$PROOF_PROJECT" --region us-central1 \
  --experiment "$PROOF_TAG" \
  --grades docs/research/google-ui-2026-10-24/original-native-grades.json \
  --output "artifacts/runs/$PROOF_TAG-publish" --allow-cloud-actions
```

Open Experiments in the same project/region, select the experiment/run and its Metrics
tab. Parameters identify the metric and the exact input file hash. This reproduction
hashes the published excerpt; the historical publication hashed the original full file.
The bounded helper accepts one complete metric/candidate; it is not a general publisher
for failed or multi-metric runs. A production publisher must preserve those states too.

Submit the saved traces through the CLI (Google grades them again):

```bash
uv run agents-cli eval submit \
  --dataset docs/research/google-ui-2026-10-24/saved-traces.json \
  --dest "gs://$PROOF_BUCKET/$PROOF_TAG/cli" \
  --metrics final_response_match --project "$PROOF_PROJECT" --region global
```

For explicit SDK experiments, run the following once with `global` and once with
`us-central1`, using distinct output/destination names:

```bash
uv run python scripts/probe_google_ui.py submit \
  --project "$PROOF_PROJECT" --region us-central1 \
  --experiment "$PROOF_TAG-regional" \
  --dataset docs/research/google-ui-2026-10-24/saved-traces.json \
  --dest "gs://$PROOF_BUCKET/$PROOF_TAG/regional" \
  --output "artifacts/runs/$PROOF_TAG-regional" --allow-cloud-actions

uv run python scripts/probe_google_ui.py poll \
  --project "$PROOF_PROJECT" --region us-central1 \
  --run "artifacts/runs/$PROOF_TAG-regional/run.json" \
  --output "artifacts/runs/$PROOF_TAG-regional-readback" --allow-cloud-actions
```

`poll` reads once; pending is not success. Repeat into a fresh output if needed. Preserve
`FAILED` states and errors. The helper uses CLI-internal SDK compatibility imports to
match this pinned experiment; recheck those APIs before upgrading dependencies.

To repeat the explicit-candidate probe, use `submit --run` with the regional `run.json`
instead of `--dataset`, keep the region/project, choose a fresh `--dest`/`--output`, and
add `--candidate-model projects/YOUR_PROJECT_ID/locations/global/publishers/google/models/gemini-3.8-flash`.
This reuses the prior experiment and saved set. Use only the completed supplied
`Candidate 1` responses: missing/mismatched candidates can trigger model inference.
The historical readbacks retained identical saved requests and historical events.

Retrieve CLI-native detailed results with `agents-cli eval results --run-id RESOURCE_NAME
--project YOUR_PROJECT_ID --region REGION --output NEW_DIRECTORY`. This reads completed
results rather than re-executing an agent. Inspect API results and the UI separately.

Native Evaluation URLs follow
`https://console.cloud.google.com/agent-platform/evaluation/locations/REGION/evaluationExperiments/EXPERIMENT_ID?project=YOUR_PROJECT_ID`.
They require your own resource IDs and access; the sanitized evidence URLs are templates.

## Failures, upstream breadcrumbs and remaining gaps

- Explicit SDK `Metric(name="final_response_match")` failed with `ValueError: Unsupported
  metric type or invalid metric name: final_response_match`. `final_response_match_v2`
  succeeded. The CLI accepts the unversioned alias; that is not the same as this SDK object.
- [Agents CLI issue #54](https://github.com/google/agents-cli/issues/54) remained open on
  the observation date. It reports experiment visibility problems. The installed SDK
  now auto-creates experiments, so its older description is not a complete explanation
  for the observed UI listing/result-rendering gap. No root cause or guaranteed workaround
  was established; do not claim all SDK runs are invisible or this is permanently broken.
- Browser-only CSV upload remains untested. The blocking extension setting is local
  tooling, not evidence of a Google service defect. No evaluation was submitted that way.
- Importing arbitrary precomputed grades as a completed native Evaluation run is unproven.
  Public SDK RESULT-item creation alone does not establish UI linkage. Local ADK Web is
  a separate surface and was not exercised in this cloud UI experiment.
- Exact incremental judge cost was not measured. The proof bucket held 16 objects totaling
  99,251 bytes at capture time. No hosted agent was started. Evidence does not establish
  a zero-cost workflow or a scalable storage/dashboard integration.

Recommended follow-up: keep the existing native grading/gate/report workflow; make an
Experiments publisher optional and separate if needed. Preserve run/case/attempt identity,
original metrics, unavailable grades, provenance and report links. Do not let publication
failure change the recorded evaluation verdict, retry agents or erase valid low scores.
Retest the richer Evaluation UI with both passing and failed cases before promising it.

Google documents [logging metrics to Experiments](https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/experiments/log-data)
and [evaluation/experiment integration](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/eval-python-sdk/run-evaluation).
Those capabilities do not by themselves prove automatic UI registration by local ADK or
CLI grading. The observations and native readbacks above are the evidence for this POC.
