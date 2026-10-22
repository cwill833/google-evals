# Quota-aware grading and explicit recovery

The harness captures all agent conversations first, then submits batches of saved traces
to Google's native `agents-cli eval grade`. Each batch selects one native metric. There
is no custom judge or score adjustment. Recovery never imports or executes the configured
agent and never retries a valid score, including zero.

## Configuration

Add these optional fields to an ADK pack's config **before starting its run**:

```json
{
  "grading_queue": "../../artifacts/grading-queue",
  "queue_budget_qps": 0.5,
  "queue_wait_timeout": 3600,
  "queue_cooldown_seconds": 30,
  "grading_batch_size": 100,
  "max_grading_attempts": 3,
  "qps": 0.5
}
```

These are the defaults except `qps`, whose existing default is 2 and is capped by the
shared budget. Paths are relative to the config file. The default queue directory is
`project_root/artifacts/grading-queue`. All cooperating runs using the same Google quota
must explicitly share a queue directory when they have different project roots. The
budget is immutable for an existing directory; mismatched settings fail instead of
silently raising another run's limit. Choose a new directory only after outstanding
jobs/reservations have drained and the operator has reviewed the new quota budget.

The POSIX queue admits native grading subprocesses in FIFO order, with one active
subprocess per queue. It persists reservations of case-metric computations divided by
effective QPS, including failed jobs, to avoid immediate retry bursts. The native CLI
also receives the capped QPS. Observed 429/RESOURCE_EXHAUSTED messages add a shared
cooldown after the batch. Waiting tickets from dead processes are reaped using locks.
The native child inherits the dispatch and run locks: a dead parent cannot unlock a
still-running grader and cause concurrent recovery. A stuck child must exit or be
explicitly stopped; there is no unsafe automatic lease expiry.

## Recovery command

```bash
uv run python -m adk_eval_harness run \
  --config packs/support/config.json --output artifacts/runs/support-001

uv run python -m adk_eval_harness recover \
  --config packs/support/config.json \
  --from-run artifacts/runs/support-001 \
  --output artifacts/runs/support-001-recovery-1
```

Recovery is explicit and requires a fresh output directory. It copies the original
traces, native files, attempt ledger and original manifest/gate snapshots, then appends
attempts only for missing or errored case-metric pairs. Each invocation attempts each
eligible pair once, up to the configured cumulative cap. A successful grade becomes
permanent even when it fails the quality threshold. Low scores cannot be improved by
retrying through this API. Google SDK internal transport retries remain upstream behavior;
the harness cap counts native grading jobs, not hidden service requests.

The source manifest and original failed gate are unchanged. A separate `recovery-link.json`
records the single successor to prevent repeated forks from resetting retry counts.
Further recovery must continue from that successor. A failure while copying evidence does
not claim the source. Existing active runs, incomplete generation, changed source/config/
policy/client versions, corrupted results and ambiguous executions are rejected. In
particular, a terminated native process that already wrote valid scores is blocked for
inspection rather than regrading possibly completed work. A parent killed before recording
the final exit code is recoverable only if no native result exists.

Control-C stops scheduling subsequent jobs and retains a recoverable interrupted grading
record. Agent-generation interruption remains nonrecoverable here: the harness cannot
assume that tools are safe to rerun. This recovery path is schema 3 only; old evidence
remains replayable through the gate but cannot be silently migrated across code changes.

## Evidence and verdicts

The manifest stores every attempt's exact trace and metric config hashes, native command,
exit code, queue timings and native result hash. The gate resolves the first valid native
payload for each required pair without modifying its score, explanation or rubrics.
Its combined coverage counts are explicitly a **derived view**, not a Google-produced
merged result file. The individual native JSON/HTML result files remain authoritative.

Reports include `grading_attempts`, `pending_grades` and `recovered_grading_failures`.
Original failures remain visible as recovered warnings when recovery succeeds. Any
unavailable required grade still blocks evaluation health; valid low scores still block
quality according to the unchanged policy. Ancestor hashes and attempt prefixes are
verified so dropping failed history cannot accidentally yield an approved recovery.
Local evidence is hash-bound, not cryptographically signed against a malicious operator.

## Verification and scope

```bash
# No cloud calls: real process contention plus synthetic recovery contract tests.
uv run python scripts/queue_load_test.py --output artifacts/runs/queue-local-new

# Billable native run: 200 ADK cases, interrupt first grader, recover pending grades.
uv run python scripts/queue_load_test.py --output artifacts/runs/queue-native-new \
  --native-cases 200 --native-qps 0.5
```

For a clean Linux extraction of the public ZIP, use the mounts in STATEFUL_PASS.md and
run `scripts/linux_verify_queue.py --native-cases 200`. It installs locked dependencies,
runs all tests, replays historical controls, repeats 100/500/1,000-case local workloads,
and runs the queue/interruption/recovery test. Synthetic grades verify orchestration and
failure handling only; they are never presented as hosted quality evidence.

This is a one-host local-filesystem queue, not a distributed scheduler or automatic cloud
quota discovery. Other applications and runs using different directories can still exhaust
the same project quota. Managed multi-turn metrics may make multiple underlying judge
calls, so a case-metric budget cannot guarantee zero throttling. Native SDK calls still
materialize batches in memory; generation and the combined gate retain whole-suite data.
The defaults suit this POC and must be tuned against a project's workload and quota.
Use a filesystem with working SQLite and POSIX locking. A Docker Desktop host bind
mount produced a SQLite disk-I/O error under 16-worker contention in this pass; that
failure is preserved. The Linux verifier therefore uses `--queue-root` on the container
filesystem while retaining logs on the host mount. No Windows, NFS/multi-host locking,
arbitrary agent side-effect isolation or fleet-scale SLA is claimed.

Google documents separate trace generation and grading in its
[evaluation guide](https://google.github.io/agents-cli/guide/evaluation/), and external
service limits in its [quota documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/quotas).


## Measured results — 2026-10-21

**PASS within the documented single-host Python ADK scope.** The corrected public
package installed into a clean ARM64 Linux container with **220 tests passing**.
Sixteen competing subprocesses passed FIFO order, nonoverlap, QPS capping and budget
spacing (0.88 s total; local synthetic scheduling units, no Google calls).

The hosted test generated **200 real ADK cases once**. It intentionally terminated the
first native grading process (100 cases), retained that failure and its full rate
reservation, then graded the other 100. The initial gate correctly failed with
100 unavailable grades. One explicit recovery requested exactly those
missing pairs, using identical traces and no generation command. The recovered gate
passed with **200/200 native final_response_match_v2 grades at 1.0**. The original
failed gate and all 100 recovered-failure records remain visible. No valid score was
retried, changed or discarded. Native SDK logs contained **0 retry messages**
and **0 metric-processing errors**.

Initial run: 450.34 s; recovery: 221.57 s;
combined 11.20 minutes, including the deliberately wasted reservation.
The fixture uses a real deterministic ADK agent/tool with no agent LLM calls; these
are actual hosted Google grades, not a measurement of arbitrary LLM-agent latency.

Repeated local capacity: 100/500/1,000 cases, 300/1,500/3,000 turns. At 1,000 cases,
generation took 6.97 s, gate processing
0.49 s, peak worker RSS 560 MiB.
All nine missing-grade, unavailable-grade and low-score controls failed as expected.
Those gate-load scores are synthetic, separately labeled from the native run.
Historical native finance/support/fault controls also passed offline replay.

The first Linux queue attempt failed on SQLite I/O over a Docker Desktop host mount,
before hosted grading. Its failure report is retained alongside the successful native
filesystem run. A separate test fixed false cooldown detection from ordinary numbers
such as 0.429; no native scores were changed.

Evidence: `artifacts/evidence/queue-recovery/summary.json`, `initial/`, `recovery/`,
`verification/` and `initial-filesystem-failure/`. Native trace/result/config artifacts
were verified byte-identical after export (15 bindings). Source/config/policy
and all executable/test files in the final public archive match the tested candidate;
only documentation and curated evidence are added afterward. Candidate SHA-256:
`64f1fe59c9e0ff750bc2c5f30e2552055a1344f1e64007fa1431bd0dc2b5904c`. Final checksum and audit are archive sidecars.
