"""Contract tests for observed trace integrity and deterministic expectations."""
import copy
import json
import pytest
from tests.fixture_runs import make_run

from adk_eval_harness.gate import digest, evaluate, observation_checks, trace_checks


def case_with_events(events, response="{}"):
    return {
        "agent_data": {"turns": [{"turn_index": 0, "events": events}]},
        "responses": [{"response": {"parts": [{"text": response}]}}],
    }


def final_event(text="{}", thought=False):
    return {"author": "agent", "content": {"parts": [{"text": text, "thought": thought}]}}


def test_explicit_null_expectations_require_present_keys():
    calls = [{"name": "set_value", "args": {}}]
    results = [{"name": "set_value", "response": {}}]
    rules = {"calls": [{"name": "set_value", "args": {"value": None}}],
             "results": [{"name": "set_value", "values": {"value": None}}]}
    assert len(observation_checks(calls, results, rules)) == 2
    calls[0]["args"]["value"] = None
    results[0]["response"]["value"] = None
    assert observation_checks(calls, results, rules) == []


def test_thought_text_is_not_part_of_json_answer():
    event = final_event()
    event["content"]["parts"].insert(0, {"text": "Reasoning about JSON", "thought": True})
    case = case_with_events([event, final_event("Later internal reasoning", thought=True)])
    assert trace_checks(case, {"turns": {"0": {"json_response_expected": {}}}}) == []


def test_reused_call_id_after_response_is_structurally_invalid():
    pair = [
        {"author": "agent", "content": {"parts": [{"function_call": {"name": "read", "id": "same"}}]}},
        {"author": "agent", "content": {"parts": [{"function_response": {"name": "read", "id": "same", "response": {}}}]}},
    ]
    case = case_with_events(pair + copy.deepcopy(pair) + [final_event()])
    assert "invalid or duplicate call ID" in trace_checks(case, {})


def test_candidate_response_must_match_observed_final_text():
    case = case_with_events([final_event("Failure")], response="Success")
    assert "final response differs from observed last-turn response" in trace_checks(case, {})
    case["responses"][0]["response"]["parts"][0]["text"] = "Failure"
    assert trace_checks(case, {}) == []


def test_desynchronized_native_candidate_cannot_count_as_behavioral_failure(tmp_path):
    run = make_run(tmp_path)
    manifest = json.loads((run / "manifest.json").read_text())
    entry = manifest["suites"]["single"]
    trace_path, result_path = run / entry["trace"], run / entry["results"]
    trace, result = json.loads(trace_path.read_text()), json.loads(result_path.read_text())
    trace["eval_cases"][0]["responses"][0]["response"]["parts"] = [{"text": "Invented successful answer"}]
    result["evaluation_dataset"][0]["eval_cases"] = trace["eval_cases"]
    trace_path.write_text(json.dumps(trace))
    result_path.write_text(json.dumps(result))
    entry.update(trace_sha256=digest(trace_path), results_sha256=digest(result_path))
    (run / "manifest.json").write_text(json.dumps(manifest))
    report = evaluate(run, run / "policy.json")
    assert not report["passed"]
    assert not report["evaluation_complete"]
    assert any("differs from observed" in error for error in report["errors"])
    assert all(score["native_metric_name"] for score in report["scores"])


@pytest.mark.parametrize("requested,emitted,summary,passes", [
    ("final_response_match", "final_response_match_v2", "final_response_match_v2", True),
    ("final_response_match_v2", "final_response_match_v2", "final_response_match_v2", True),
    ("final_response_match_v2", "final_response_match_v3", "final_response_match_v3", False),
    ("final_response_match_v2", "final_response_match", "final_response_match", False),
    ("final_response_match_v2", "final_response_match_v2", "final_response_match_v3", False),
])
def test_native_metric_version_requests_are_enforced(tmp_path, requested, emitted, summary, passes):
    run = make_run(tmp_path)
    manifest = json.loads((run / "manifest.json").read_text())
    policy = json.loads((run / "policy.json").read_text())
    policy["thresholds"][requested] = policy["thresholds"].pop("final_response_match")
    policy["suites"]["single"]["metrics"] = [requested]
    (run / "policy.json").write_text(json.dumps(policy))
    manifest["policy_sha256"] = digest(run / "policy.json")
    entry = manifest["suites"]["single"]
    result_path = run / entry["results"]
    result = json.loads(result_path.read_text())
    for row in result["eval_case_results"]:
        candidate = row["response_candidate_results"][0]
        metric = next(iter(candidate["metric_results"].values()))
        metric["metric_name"] = emitted
        candidate["metric_results"] = {emitted: metric}
    result["summary_metrics"][0]["metric_name"] = summary
    result_path.write_text(json.dumps(result))
    entry["results_sha256"] = digest(result_path)
    (run / "manifest.json").write_text(json.dumps(manifest))
    report = evaluate(run, run / "policy.json")
    assert report["passed"] is passes
    assert report["evaluation_complete"] is passes
    if passes:
        scores = [s for s in report["scores"] if s["suite"] == "single"]
        assert len(scores) == 2
        assert all(s["metric"] == requested and s["native_metric_name"] == emitted for s in scores)
