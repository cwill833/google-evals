"""Release policy over native Google results; no custom quality evaluator."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re



def read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    return json.loads(
        path.read_text(), object_pairs_hook=pairs, parse_constant=constant
    )


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_equal(actual, expected):
    """JSON numbers may compare numerically; booleans are a distinct JSON type."""
    if type(actual) in (int, float) and type(expected) in (int, float):
        return (type(actual) is int or math.isfinite(actual)) and (type(expected) is int or math.isfinite(expected)) and actual == expected
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(json_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(json_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def compact(value, schema=None):
    """Normalize optional SDK fields without deleting nulls from JSON payloads.

    Walk the pinned SDK's type annotations, not arbitrary dictionary keys. Unknown
    fields and Any-valued payloads remain untouched and therefore compare exactly.
    This does not validate/coerce values through Pydantic (e.g. bool into int).
    """
    from typing import Any, Union, get_args, get_origin
    from types import UnionType
    if schema is None:
        from agentplatform._genai.types import EvalCase
        schema = EvalCase
    origin, args = get_origin(schema), get_args(schema)
    if origin in (Union, UnionType):
        choices = [a for a in args if a is not type(None)]
        if len(choices) != 1:
            return value  # Ambiguous shapes fail exact comparison, not loose normalization.
        return compact(value, choices[0])
    fields = getattr(schema, 'model_fields', None)
    if fields is not None and isinstance(value, dict):
        return {key: compact(item, fields[key].annotation) if key in fields else item
                for key, item in value.items()
                if not (key in fields and item is None and type(None) in get_args(fields[key].annotation))}
    if origin is list and isinstance(value, list):
        return [compact(item, args[0]) for item in value]
    if origin is dict and isinstance(value, dict) and args[1] is not Any:
        return {key: compact(item, args[1]) for key, item in value.items()}
    return value


def comparable(case):
    c = compact(case)
    for turn in c.get("agent_data", {}).get("turns", []):
        turn["events"] = [e for e in turn.get("events", []) if e.get("content")]
    return c


def text(content):
    return "".join(p.get("text", "") or "" for p in (content or {}).get("parts", [])
                   if not p.get("thought"))


def requested_metric_name(native_name, requested):
    """Resolve server version suffixes only for explicitly unversioned requests."""
    if native_name in requested:
        return native_name
    base = re.sub(r"_v[0-9]+$", "", native_name)
    return base if base in requested else native_name


def trace_checks(case, rules):
    errors = []
    calls = []
    results = []
    state = {}
    turns = case.get("agent_data", {}).get("turns", [])
    if len(turns) < rules.get("min_turns", 1):
        errors.append("insufficient actual turns")
    for i, turn in enumerate(turns):
        if turn.get("turn_index") != i:
            errors.append("invalid turn index")
        pending = {}
        seen_call_ids = set()
        turn_calls, turn_results = [], []
        for event in turn.get("events", []):
            state.update(event.get("state_delta") or {})
            for part in (event.get("content") or {}).get("parts", []):
                fc = part.get("function_call")
                fr = part.get("function_response")
                if fc:
                    calls.append(fc)
                    turn_calls.append(fc)
                    name = fc["name"]
                    args = fc.get("args", {})
                    call_id = fc.get("id")
                    if not call_id or call_id in seen_call_ids:
                        errors.append("invalid or duplicate call ID")
                    seen_call_ids.add(call_id)
                    pending[call_id] = name
                if fr:
                    results.append(fr)
                    turn_results.append(fr)
                    call_id = fr.get("id")
                    if pending.pop(call_id, None) != fr["name"]:
                        errors.append("unmatched tool response")
        if pending:
            errors.append("missing tool responses")
        turn_rules = rules.get("turns", {}).get(str(i), {})
        errors.extend(f"turn {i}: {e}" for e in observation_checks(turn_calls, turn_results, turn_rules))
        for key, expected in turn_rules.get("state_equals", {}).items():
            if key not in state or not json_equal(state[key], expected):
                errors.append(f"turn {i}: state {key} mismatch")
        if "json_response_expected" in turn_rules:
            content = next((e.get("content") for e in reversed(turn.get("events", []))
                            if e.get("author") != "user" and text(e.get("content"))), None)
            try:
                if not json_equal(json.loads(text(content)), turn_rules["json_response_expected"]):
                    errors.append(f"turn {i}: incorrect JSON response")
            except (ValueError, TypeError):
                errors.append(f"turn {i}: invalid JSON response")
    if any(not key.isdigit() or int(key) >= len(turns) for key in rules.get("turns", {})):
        errors.append("missing required turn")
    errors.extend(observation_checks(calls, results, rules))
    responses = case.get("responses", [])
    if len(responses) != 1 or not text(responses[0].get("response")):
        errors.append("missing or ambiguous final response")
    else:
        observed = next((e.get("content") for e in reversed(turns[-1].get("events", []))
                         if e.get("author") != "user" and text(e.get("content"))), None) if turns else None
        if text(observed) != text(responses[0].get("response")):
            errors.append("final response differs from observed last-turn response")
    if len(responses) == 1 and (rules.get("json_response") or "json_response_expected" in rules):
        try:
            result = json.loads(text(responses[0]["response"]))
            if not json_equal(result, rules.get("json_response_expected", result)):
                errors.append("incorrect JSON response")
        except (ValueError, TypeError):
            errors.append("invalid JSON response")
    return errors


def observation_checks(calls, results, rules):
    errors = []
    names = [c["name"] for c in calls]
    for expected in rules.get("calls", []):
        if not any(
            c["name"] == expected["name"]
            and all(
                k in c.get("args", {}) and json_equal(c["args"][k], v)
                for k, v in expected.get("args", {}).items()
            )
            for c in calls
        ):
            errors.append(f"missing expected tool call: {expected}")
    for name in rules.get("forbidden", []):
        if name in names:
            errors.append(f"CRITICAL: forbidden tool {name}")
    for expected in rules.get("results", []):
        if not any(
            r["name"] == expected["name"] and all(
                k in r.get("response", {}) and json_equal(r["response"][k], v)
                for k, v in expected.get("values", {}).items()
            ) for r in results
        ):
            errors.append(f"missing expected tool result: {expected}")
    for first, second in rules.get("before", []):
        if (
            first not in names
            or second not in names
            or names.index(first) >= names.index(second)
        ):
            errors.append(f"wrong sequence {first} -> {second}")
    for name, expected in rules.get("call_counts", {}).items():
        if names.count(name) != expected:
            errors.append(f"incorrect call count for {name}")
    return errors


def evaluate(run, policy_path, assertion_hook=None):
    errors = []
    scores = []
    warnings = []
    behavior_errors = []
    grading_attempts = {}
    pending_grades = []
    recovered_grading_failures = 0
    try:
        policy = read(policy_path)
        manifest = read(run / "manifest.json")
        from adk_eval_harness.integrity import verify_telemetry
        telemetry_integrity, _ = verify_telemetry(run, manifest)
        errors.extend(telemetry_integrity['errors'])
        if policy.get("assertion_hook"):
            from adk_eval_harness.config import symbol
            assertion_hook = symbol(policy["assertion_hook"])
        from adk_eval_harness.validation import validate_policy
        validate_policy(policy, assertion_hook)
        roles = policy.get("metric_roles", {})
        if any(k not in policy["thresholds"] or v not in ("blocking", "advisory") for k, v in roles.items()):
            raise ValueError("invalid metric role")
        if "advisory" in roles.values() and not policy.get("calibration_rationale"):
            raise ValueError("advisory metrics require a calibration rationale")
        if policy.get("schema_version") not in (1, 2) or manifest.get("schema_version") not in (1, 2, 3):
            raise ValueError("unknown schema version")
        ledger_mode = manifest['schema_version'] == 3
        if ledger_mode:
            from adk_eval_harness.lineage import verify_lineage
            verify_lineage(run, manifest)
        if ledger_mode and manifest.get('generation_complete') is not True:
            errors.append('generation is incomplete; grading recovery cannot complete agent execution')
        if not policy.get("suites") or not policy.get("thresholds"):
            raise ValueError("empty release policy")
        if manifest.get("policy_sha256") != digest(policy_path):
            raise ValueError("policy hash mismatch")
        expected_versions = {
            "google-agents-cli": "1.11.0",
            "google-adk": "2.12.0",
            "google-cloud-aiplatform": "2.2.0",
        }
        if manifest.get("versions") != expected_versions:
            raise ValueError("unverified client versions")
        if manifest.get("errors"):
            errors.extend(manifest["errors"])
        commands = manifest.get("commands", [])
        if not commands or any(c.get("exit_code") != 0 for c in commands):
            errors.append("missing or failed CLI execution")
        if ledger_mode and any(type(c.get('exit_code')) is not int for c in commands):
            errors.append('generation/preflight command exit codes must be integers')
        if ledger_mode and any(c.get('argv', [])[1:3] == ['eval', 'grade'] for c in commands):
            errors.append('schema 3 grading commands must be recorded in the attempt ledger')
        if set(manifest["suites"]) != set(policy["suites"]):
            errors.append("suite coverage mismatch")
        for suite, requirements in policy["suites"].items():
            if not requirements.get("cases") or not requirements.get("metrics"):
                raise ValueError("empty required coverage")
            if len(requirements["metrics"]) != len(set(requirements["metrics"])):
                raise ValueError("duplicate required metrics")
            if suite not in manifest["suites"]:
                continue
            entry = manifest["suites"][suite]
            trace_path = (run / entry["trace"]).resolve()
            if not trace_path.is_relative_to(run.resolve()):
                raise ValueError("artifact path escapes run")
            if digest(trace_path) != entry['trace_sha256']:
                raise ValueError("artifact hash mismatch")
            trace = read(trace_path)["eval_cases"]
            if ledger_mode:
                from adk_eval_harness.attempts import resolve
                result, history, pending = resolve(run, suite, entry, requirements)
                grading_attempts[suite] = history
                pending_grades.extend({'suite':suite, 'case':cid, 'metric':metric} for cid, metric in pending)
                for attempt in history:
                    for failure in attempt['errors']:
                        if failure['recovered']:
                            recovered_grading_failures += 1
                            warnings.append(
                                f"{suite}/{failure['case']}/{failure['metric']}: recovered grading failure "
                                f"from attempt {attempt['attempt_index']}: {failure['message']}")
                if pending:
                    errors.append(f'{suite}: {len(pending)} native grades remain unavailable after recorded attempts')
            else:
                result_path = (run / entry['results']).resolve()
                if not result_path.is_relative_to(run.resolve()):
                    raise ValueError('artifact path escapes run')
                if digest(result_path) != entry['results_sha256']:
                    raise ValueError('artifact hash mismatch')
                result = read(result_path)
            ids = [c["eval_case_id"] for c in trace]
            if len(ids) != len(set(ids)) or set(ids) != set(requirements["cases"]):
                raise ValueError(f"{suite}: case coverage mismatch")
            result_cases = [
                c
                for dataset in result["evaluation_dataset"]
                for c in dataset["eval_cases"]
            ]
            if not json_equal([comparable(c) for c in trace], [comparable(c) for c in result_cases]):
                raise ValueError(f"{suite}: grades do not match traces")
            rows = result["eval_case_results"]
            if any(type(r.get("eval_case_index")) is not int for r in rows):
                raise ValueError("case indices must be integers")
            if len(rows) != len(trace) or {r["eval_case_index"] for r in rows} != set(
                range(len(trace))
            ):
                raise ValueError(f"{suite}: result case index coverage mismatch")
            for row in rows:
                case = trace[row["eval_case_index"]]
                cid = case["eval_case_id"]
                case_errors = list(
                    f"{suite}/{cid}: {e}"
                    for e in (trace_checks(case, requirements["cases"][cid]) +
                              (assertion_hook(case, requirements["cases"][cid]) if assertion_hook else []))
                )
                errors.extend(case_errors)
                structural = {f"{suite}/{cid}: {e}" for e in trace_checks(case, {})}
                behavior_errors.extend(e for e in case_errors if e not in structural)
                candidates = row["response_candidate_results"]
                if (
                    len(candidates) != 1
                    or type(candidates[0].get("response_index")) is not int
                    or candidates[0]["response_index"] != 0
                ):
                    raise ValueError("candidate coverage mismatch")
                metrics = candidates[0]["metric_results"]
                if any(m.get("metric_name") != name for name, m in metrics.items()):
                    raise ValueError("metric name mismatch")
                # Bare requests accept the server-resolved version; explicit pins must match exactly.
                normalized = {
                    requested_metric_name(k, requirements["metrics"]): v for k, v in metrics.items()
                }
                if len(normalized) != len(metrics) or set(normalized) != set(
                    requirements["metrics"]
                ):
                    raise ValueError(f"{suite}/{cid}: metric coverage mismatch")
                for metric, m in normalized.items():
                    score = m.get("score")
                    threshold = policy["thresholds"][metric]
                    if (
                        type(threshold) not in (float, int)
                        or not math.isfinite(threshold)
                        or not 0 <= threshold <= 1
                    ):
                        raise ValueError("invalid threshold")
                    if (
                        m.get("error_message")
                        or type(score) not in (float, int)
                        or not math.isfinite(score)
                        or not 0 <= score <= 1
                    ):
                        errors.append(
                            f"{suite}/{cid}/{metric}: missing, malformed, or unavailable grade"
                        )
                    elif score < threshold:
                        target = warnings if roles.get(metric, "blocking") == "advisory" else errors
                        target.append(f"{suite}/{cid}/{metric}: {score} < {threshold}")
                        if target is errors:
                            behavior_errors.append(target[-1])
                    scores.append(
                        {
                            "suite": suite,
                            "case": cid,
                            "metric": metric,
                            "native_metric_name": m["metric_name"],
                            "score": score,
                            "threshold": threshold,
                            "role": roles.get(metric, "blocking"),
                        }
                    )
            summaries = result["summary_metrics"]
            if len(summaries) != len(requirements["metrics"]):
                raise ValueError("summary metric coverage mismatch")
            if {requested_metric_name(s["metric_name"], requirements["metrics"]) for s in summaries} != set(
                requirements["metrics"]
            ):
                raise ValueError("summary metric names mismatch")
            for summary in summaries:
                if any(
                    type(summary.get(k)) is not int
                    for k in ["num_cases_total", "num_cases_valid", "num_cases_error"]
                ):
                    raise ValueError("summary counts must be integers")
                if (
                    summary.get("num_cases_total") != len(trace)
                    or summary.get("num_cases_valid") != len(trace)
                    or summary.get("num_cases_error") != 0
                ):
                    errors.append(f"{suite}: incomplete summary")
    except Exception as exc:
        errors.append(f"Fail closed: {type(exc).__name__}: {exc}")
    return {"passed": not errors, "errors": errors, "warnings": warnings, "scores": scores,
            "evaluation_complete": not any(e not in behavior_errors for e in errors),
            'grading_attempts':grading_attempts, 'pending_grades':pending_grades,
            'recovered_grading_failures':recovered_grading_failures}


def write_report(report, path):
    path.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("run", type=Path)
    p.add_argument(
        "--policy", type=Path, default=Path("tests/eval/policies/release.json")
    )
    args = p.parse_args()
    report = evaluate(args.run, args.policy)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
