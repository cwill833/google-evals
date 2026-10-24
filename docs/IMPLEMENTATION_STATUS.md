# Implementation status

Updated 2026-10-22 after source/evidence separation. **Source-only verification: PASS.** See RESULTS.md for verification, native evidence, preserved failures and remaining scope limits.

- Configurable bounded local/hosted case concurrency, sequential turns, unique user/session identities and fresh local agent factories.
- 1,000 local capacity cases at concurrency 8; 24 local and 100 hosted cases with fresh native Google grading.
- Grader-only recovery of 20 unavailable grades in each native run preserved original failure reports and never reran agents.
- Real local/hosted generation faults stopped dispatch, drained in-flight cases and refused recovery; cold-start failure and superseded run remain visible.
- Shared ADK-only core with configurable agent/factory, runtime, project paths, user and initial state.
- Finance and support agents use the same runner and gate; domain assertions live outside the core.
- 327 macOS/Linux regression tests without the historical archive; external native replay remains available without new cloud grading.
- Shared raw-evidence verification, JSON-type-aware assertions and schema-aware null preservation.
- Bounded session/cleanup deadlines, graceful CLI cancellation and symlink-safe public packaging.
- Authenticated hosted ADK adapter with isolated remote users/sessions, revision/source binding, no automatic write retries.
- Hosted load: 12 cases/36 turns/12 native grades, six missing grades recovered without agent reruns.
- Hosted support: 8 cases/16 turns/20 native grades; policy PASS.
- Private Cloud Run fixtures; real Linux transport probes cover anonymous denial, concurrency, state continuity and ambiguous timeout.
- Consolidated HTML/JSON reports with prompts, responses, native grades, attempt history, observed agent tokens and measured client latency.
- Raw telemetry hashes, protected native/session exports and explicit unavailable judge-token reporting.
- Shared one-host FIFO grading queue, bounded dispatch/reservations and throttling cooldown.
- Explicit immutable recovery of unavailable grades; valid scores never retry and agents never rerun.
- 200-case native interruption/recovery passed with 100 failed pairs recovered and original failure retained.
- Queue requires working local POSIX/SQLite locking; Docker Desktop host-mounted queue failed and is unsupported here.
- Fresh 200-case native load passed after fixture/dispatch corrections; fresh support 8 cases/20 grades passed with two advisories.
- Local capacity passed 1,000 three-turn cases and 500 larger five-turn cases; native and synthetic measurements are distinguished.
- Whole-pack preflight, strict assertions, current-source evidence binding, explicit metric pins, append-only completion checkpoints and per-case factories.
- Static text native ADK EvalSet importer produces a reviewed draft, without seeding expected assistant responses.
- Six executed fault cases; ten additional native grades; every fault rejected for behavior.
- Four cases prospectively frozen before their original evaluation, now regression coverage alongside the four prior finance holdouts.
- Native metrics unchanged; explicit policy-v2 advisory roles for full-conversation tool use and trajectory.
- Evaluation-health failures remain blocking regardless of metric role. No custom LLM judge.
- Open Google issues, related workarounds, absent exact-match fixes and version limits documented.
- Source-identical package provenance; raw score/rubric evidence and checksum sidecars are retained locally.
- Source-only PR/package; unit tests create synthetic fixtures and require no historical run archive.

This is a reusable Python ADK text/tool reference with small-corpus engineering calibration,
not a universal ADK compatibility claim or statistically calibrated production release gate.
Dynamic toolsets, callable instructions, custom session backends, complex multi-agent systems,
voice/media, x86_64 evaluation clients, Windows and independent cloud-account onboarding remain unverified.
The hosted server runs on x86_64 Linux; its sessions remain in memory on one instance.
Durable sessions, multi-instance operation, workload-identity transport authentication,
central result storage and a hosted report dashboard are not implemented.
The harness does not automatically isolate arbitrary real-world tool side effects.

See RESULTS.md for every score, CALIBRATION.md for policy rationale, REUSABLE_ADK.md for
integration, and UPSTREAM_ISSUES.md for the dated investigation. Prior failed gates remain
historical evidence; no upstream bug is claimed fixed and no external issue was posted.
