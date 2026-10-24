"""Live ADK/CLI inference and native Google grading with a fail-closed gate."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import signal
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.quality_gate import evaluate, write_report, trace_checks, read

CLI = ROOT / ".venv/bin/agents-cli"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def complete_trace(path, requirements):
    """Never reuse or grade a partially successful native generation as complete."""
    try:
        cases = read(path)["eval_cases"]
        ids = [case["eval_case_id"] for case in cases]
        return (
            len(ids) == len(set(ids))
            and set(ids) == set(requirements["cases"])
            and all(
                not trace_checks(case, requirements["cases"][case["eval_case_id"]])
                for case in cases
            )
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def command(args, log, records, cwd=ROOT, timeout=600, executable=None):
    prefix = executable or [str(CLI), "eval"]
    argv = [*prefix, *map(str, args)]
    print("Running: " + " ".join(argv), flush=True)
    with log.open("w") as stream:
        try:
            p = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=stream,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
            )
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            code = 124
        except KeyboardInterrupt:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            raise
    records.append(
        {
            "argv": [*(executable or ["agents-cli", "eval"]), *map(str, args)],
            "exit_code": code,
            "log": str(log),
        }
    )
    if code:
        raise RuntimeError(f"CLI exited {code}; see {log}")


def generate_multi(out, records, suite="multi_turn"):
    trace = out / "traces.json"
    command(
        ["--dataset", ROOT / f"tests/eval/datasets/{suite}/conversations.json",
         "--output", trace], out / "generate.log", records,
        executable=[sys.executable, str(ROOT / "scripts/generate_conversations.py")],
        timeout=1800,
    )
    return trace


def verify_holdout(policy):
    if "holdout" not in policy["suites"]:
        return
    freeze = read(ROOT / "tests/eval/datasets/holdout/freeze.json")
    for name, sha in freeze["sha256"].items():
        if digest(ROOT / name) != sha:
            raise ValueError("Frozen holdout changed")
    frozen = read(ROOT / "tests/eval/policies/holdout.json")
    if policy["suites"]["holdout"] != frozen["suites"]["holdout"] or any(
        policy["thresholds"].get(k) != v for k, v in frozen["thresholds"].items()
    ):
        raise ValueError("Holdout policy differs from frozen policy")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="tests/eval/policies/release.json")
    parser.add_argument("--output", help="New run directory; must not exist")
    parser.add_argument("--fault-injection", action="store_true",
                        help="Run the isolated deliberately faulty ADK agent; the gate must fail")
    parser.add_argument(
        "--reuse-traces",
        type=Path,
        help="Reuse unchanged suites after verifying source hashes; regenerate changed suites and regrade everything with Google",
    )
    args = parser.parse_args()
    if args.fault_injection:
        if args.reuse_traces or args.policy != "tests/eval/policies/release.json":
            parser.error("Fault injection cannot be combined with trace reuse or custom policies")
        args.policy = "tests/eval/policies/faults.json"
    os.chdir(ROOT)
    os.environ.update(CI="1", AGENTS_CLI_TELEMETRY="0")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    run = Path(
        args.output
        or ("artifacts/runs/" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    ).resolve()
    run.mkdir(parents=True, exist_ok=False)
    policy_path = Path(args.policy).resolve()
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy_sha256": digest(policy_path),
        "commands": [],
        "suites": {},
        "versions": {},
        "errors": [],
        "execution_mode": "controlled_fault_injection" if args.fault_injection else "release",
    }
    manifest["source_sha256"] = {
        str(p.relative_to(ROOT)): digest(p)
        for folder in ["agent", "scripts", "tests"]
        for p in (ROOT / folder).rglob("*")
        if p.is_file()
        and not any(
            part.startswith(".") or part == "__pycache__"
            for part in p.relative_to(ROOT).parts
        )
    }
    manifest["source_sha256"].update({
        name: digest(ROOT / name) for name in
        ["pyproject.toml", "uv.lock", "agents-cli-manifest.yaml", ".python-version"]
    })
    try:
        for package, version in [
            ("google-agents-cli", "1.11.0"),
            ("google-adk", "2.12.0"),
            ("google-cloud-aiplatform", "2.2.0"),
        ]:
            actual = importlib.metadata.version(package)
            if actual != version:
                raise ValueError(f"{package}: expected {version}, got {actual}")
            manifest["versions"][package] = actual
        if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
            raise ValueError("GOOGLE_CLOUD_PROJECT is required for managed evaluation")
        import google.auth
        from google.auth.transport.requests import Request

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(Request())  # Never print or serialize credentials.
        policy = json.loads(policy_path.read_text())
        verify_holdout(policy)
        reuse_manifest = None
        if args.reuse_traces:
            args.reuse_traces = args.reuse_traces.resolve()
            reuse_manifest = json.loads(
                (args.reuse_traces / "manifest.json").read_text()
            )
            if reuse_manifest.get("versions") != manifest["versions"]:
                raise ValueError("Reused trace client versions differ")
            if {k: v for k, v in manifest["source_sha256"].items() if k.startswith("agent/") or k == "scripts/generate_conversations.py"} != {
                k: v for k, v in reuse_manifest["source_sha256"].items() if k.startswith("agent/") or k == "scripts/generate_conversations.py"
            }:
                raise ValueError("Reused trace agent source set changed")
            manifest["trace_origin"] = str(args.reuse_traces)
        save(run / "policy.json", policy)
        command(["metric", "list"], run / "metric-list.txt", manifest["commands"])
        for suite in policy["suites"]:
            out = run / suite
            out.mkdir()
            suite_sources = {
                name: sha
                for name, sha in manifest["source_sha256"].items()
                if name.startswith(f"tests/eval/datasets/{suite}/")
            }
            can_reuse = (
                reuse_manifest is not None
                and suite in reuse_manifest["suites"]
                and bool(suite_sources)
                and suite_sources == {name: sha for name, sha in reuse_manifest["source_sha256"].items()
                                      if name.startswith(f"tests/eval/datasets/{suite}/")}
            )
            if can_reuse:
                original = reuse_manifest["suites"][suite]
                source = (args.reuse_traces / original["trace"]).resolve()
                if not source.is_relative_to(args.reuse_traces):
                    raise ValueError("Reused trace path escapes its run")
                if digest(source) != original["trace_sha256"]:
                    raise ValueError("Reused trace hash mismatch")
                can_reuse = complete_trace(source, policy["suites"][suite])
            if can_reuse:
                trace = out / "traces.json"
                shutil.copyfile(source, trace)
                manifest.setdefault("reused_traces", {})[suite] = {
                    "sha256": digest(source),
                    "source": str(source),
                }
            elif (ROOT / f"tests/eval/datasets/{suite}/conversations.json").exists():
                trace = generate_multi(out, manifest["commands"], suite)
            else:
                trace = out / "traces.json"
                command(
                    [
                        "generate",
                        "--dataset",
                        ROOT / f"tests/eval/datasets/{suite}/cases.json",
                        "--output",
                        trace,
                        "--concurrency",
                        "1",
                    ],
                    out / "generate.log",
                    manifest["commands"],
                    cwd=ROOT / "tests/fault_project" if args.fault_injection else ROOT,
                )
            if args.fault_injection:
                cases = read(trace)["eval_cases"]
                ids = [case["eval_case_id"] for case in cases]
                if len(ids) != len(set(ids)) or set(ids) != set(policy["suites"][suite]["cases"]):
                    raise ValueError("Incomplete fault injection execution")
            elif not complete_trace(trace, policy["suites"][suite]):
                raise ValueError(
                    f"{suite}: incomplete trace or deterministic assertion failure; see native trace/log"
                )
            grades = out / "grades"
            grades.mkdir()
            command(
                [
                    "grade",
                    "--traces",
                    trace,
                    "--config",
                    f"tests/eval/configs/{suite}.yaml",
                    "--output",
                    grades,
                    "--qps",
                    "2",
                ],
                out / "grade.log",
                manifest["commands"],
            )
            results = list(grades.glob("results_*.json"))
            if len(results) != 1:
                raise ValueError("Expected exactly one native result file")
            manifest["suites"][suite] = {
                "trace": str(trace.relative_to(run)),
                "results": str(results[0].relative_to(run)),
                "trace_sha256": digest(trace),
                "results_sha256": digest(results[0]),
            }
    except Exception as exc:
        # Exception bodies from cloud services can include private project metadata.
        manifest["errors"].append(f"{type(exc).__name__}: {exc}")
    save(run / "manifest.json", manifest)
    report = evaluate(run, policy_path)
    write_report(report, run / "gate.json")
    print(json.dumps(report, indent=2), flush=True)
    print(f"Artifacts: {run}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
