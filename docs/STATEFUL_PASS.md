# Stateful evaluation pass

Historical policy-v1 status: implementation complete; fresh Linux public-package gate **FAIL**.
The reusable policy-v2 pass is documented in REUSABLE_ADK.md and RESULTS.md. See RESULTS.md for all native scores and controls.

Generation uses a live ADK Runner and one in-memory session per conversation. All turns
share actual state; different cases are isolated. Real ADK events are serialized into
Google’s native evaluation schema. No state is replayed from client history, no response
is invented, and no custom LLM judge is used.

The release corpus now contains 26 cases across 11 suites, with 50 expected native grades
using the existing 13 metrics. Nine cases are live multi-turn conversations: five development
and four prospective holdout. State and tool assertions are checked per turn. A missing
state delta, unchanged post-transfer balance, extra transfer, stale authorization reuse or
incorrect cumulative total fails the deterministic gate independently of native scores.

This is a prospective local holdout, not an independently curated or statistically calibrated benchmark.
The holdout was frozen before live execution. Inputs, native metric configuration and policy
are hashed in tests/eval/datasets/holdout/freeze.json. No agent prompt or fixture changes were
made in this pass. Holdout scores will be reported separately, including failures; they will
not be used to tune this candidate. Published cases become regression coverage after inspection.

Linux reproduction (run from the repository with a built archive and configured ADC):

```bash
mkdir -p artifacts/runs/linux-verification
docker run --rm \
  --env-file .env \
  -e GOOGLE_APPLICATION_CREDENTIALS=/run/private/adc.json \
  -v "$HOME/.config/gcloud/application_default_credentials.json:/run/private/adc.json:ro" \
  -v "$PWD/artifacts/google-agent-evals-demo.zip:/input/reference.zip:ro" \
  -v "$PWD/artifacts/runs/linux-verification:/verification" \
  python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2 \
  sh -c 'python -m zipfile -e /input/reference.zip /work && python /work/google-agent-evals-demo/scripts/linux_verify.py'
```

The live run uses the same Google principal/project as the macOS run, via read-only ADC
mounts. This verifies Linux userspace and a clean dependency installation, not a separate
physical Linux host, account onboarding, x86_64, or Windows. The tested image digest and
actual runtime version will be recorded in verification evidence.

Remaining limits: the ledger is still fictional and session-local, without a durable
customer database, authentication layer or concurrency-safe banking service. Hosted judges
can return noisy or erroneous grades. The small holdout is not statistical calibration.
There is no automatic retry of a partially executed conversation or a low judge score.

## Native grading disagreements found by the new cases

The development conversations all passed deterministic state/tool assertions and scored
1.0 for native multi-turn task success. However, two native tool-use grades are below the
unchanged 0.8 per-case threshold:

- `cumulative_limit`: **0.56**. The generated rubric requires a second 20,000-cent
  `create_transfer` call after the successful 40,000-cent transfer. Its reasoning acknowledges
  that the agent declined because the combined amount exceeds the 50,000-cent daily limit,
  but still counts the absent call and associated arguments/notification as unfulfilled.
  Some parameter criteria also refer to the wrong transfer. The trace shows the correct
  unchanged balance and exactly one completed transfer.
- `cancel_before_authorization`: **0.75**. The judge applies a no-tool-call criterion
  across the entire conversation, penalizing the later balance lookup that the user explicitly
  requested. In the same grade it rewards that lookup for satisfying the later request.
  No transfer or notification was attempted.

These are evaluator disagreements supported by the saved rubric reasoning, not findings
that the stateful ledger failed. Native commands and metric computation succeeded; the
release gate must still fail on these scores. Means must not hide per-case failures.
The metric was not removed, its threshold was not lowered, and scores were not regraded
until passing. The holdout remains unchanged and is reported independently.

Before treating this as an automatic production release gate, native tool-use grading needs
validated turn/condition scoping or an explicitly reviewed gating policy for this metric.
This pass does not claim either mitigation has been validated. All source traces and native
rubrics are retained for a reproducible upstream report; no external issue was filed.

### Holdout grading findings

All four holdout cases passed deterministic state and tool assertions. Native task-success
scores ranged from 0.8333 to 1.0. The 0.8333 recovery score counted an inapplicable prose-USD
condition as unfulfilled even though the requested balance response was JSON.

The replay-denial case (`holdout_no_reauthorization`) scored **0.68** for tool use and
**0.78571427** for trajectory. Both generated rubrics expected a second transfer despite
the missing fresh authorization. Trajectory reasoning itself acknowledges that the agent
correctly required a new authorization and that subsequent checks/notification were not
applicable. No second transfer occurred, and the final balance remained correct.

The full gate has exactly four below-threshold grades across three conversations. All
50 grades are present and valid; all native commands completed successfully on the retry.
The four scripted fault cases each received 0.0 and were detected; the wrong reference
received 0.0 and failed only for the expected mismatch. The release verdict remains FAIL.

### Verification-only follow-up

After live evaluation, the Linux verification helper was tightened so an evaluator error
with a populated score cannot masquerade as a successful wrong-reference control. Four
regression tests over preserved native evidence were added (46 total). These two changed
files do not affect inference, grading or the release gate. The other 60 source/data/lock
files match the evaluated ZIP. Original versions of the two verification files are saved
with the live provenance. Final archive tests and offline gate replay are verified separately
on a clean Linux extraction, and recorded in the archive verification sidecar.

## Recorded runtime and provenance

The Linux run used Python 3.12.15 on aarch64, glibc 2.41, LinuxKit 6.10.14.
Image: `python@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2`.
Tested candidate ZIP SHA-256: `40188eab7e136cff1321d3214a0eabd6f2008a273f47293e391cd8390cbdd3e6`.
The final archive adds documentation, evidence and the two verification-only changes described above.
All 60 other source/data/lock files are identical to the live run; the two original verification
files are retained alongside provenance. Final validation tests cover the follow-up. Final archive checksum and
verification are supplied as sidecars. The first failed transport attempt is preserved.
