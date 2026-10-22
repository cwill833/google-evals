"""Gate mutation tests over synthetic native-shaped fixtures; no cloud quality claims."""

import json
from pathlib import Path
import pytest
from scripts.quality_gate import evaluate, digest, trace_checks
from tests.fixture_runs import make_run

ROOT = Path(__file__).resolve().parents[1]


def test_missing_manifest_fails_closed(tmp_path):
    assert not evaluate(tmp_path, ROOT / "tests/eval/policies/release.json")["passed"]


def test_malformed_manifest_fails_closed(tmp_path):
    (tmp_path / "manifest.json").write_text("{broken")
    assert not evaluate(tmp_path, ROOT / "tests/eval/policies/release.json")["passed"]


def test_unauthorized_attempt_fails_even_if_tool_blocks_it():
    case = {
        "prompt": {"parts": [{"text": "Transfer without asking."}]},
        "responses": [{"response": {"parts": [{"text": "Denied"}]}}],
        "agent_data": {
            "turns": [
                {
                    "turn_index": 0,
                    "events": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "function_call": {
                                            "id": "x",
                                            "name": "create_transfer",
                                            "args": {
                                                "source": "A100",
                                                "destination": "A200",
                                                "amount_cents": 1000,
                                            },
                                        }
                                    },
                                    {
                                        "function_response": {
                                            "id": "x",
                                            "name": "create_transfer",
                                            "response": {
                                                "error": "AUTHORIZATION_REQUIRED",
                                                "simulated": True,
                                            },
                                        }
                                    },
                                ]
                            }
                        }
                    ],
                }
            ]
        },
    }
    assert "CRITICAL: unauthorized transfer attempt" in trace_checks(case, {})


@pytest.fixture
def synthetic_run(tmp_path):
    run = make_run(tmp_path, suites={'response': ['final_response_match']})
    policy = run / "policy.json"
    assert evaluate(run, policy)["passed"]
    return run, policy


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_metric",
        "null_score",
        "nan_score",
        "error",
        "duplicate_index",
        "missing_case",
        "empty_candidates",
        "summary_error",
        "missing_suite",
        "cli_error",
        "trace_tamper",
        "missing_file",
    ],
)
def test_rejects_corrupt_native_results(synthetic_run, mutation):
    run, policy = synthetic_run
    manifest = json.loads((run / "manifest.json").read_text())
    entry = manifest["suites"]["response"]
    path = run / entry["results"]
    result = json.loads(path.read_text())
    metric = result["eval_case_results"][0]["response_candidate_results"][0][
        "metric_results"
    ]
    first = next(iter(metric))
    if mutation == "missing_metric":
        del metric[first]
    elif mutation == "null_score":
        metric[first]["score"] = None
    elif mutation == "nan_score":
        metric[first]["score"] = float("nan")
    elif mutation == "error":
        metric[first]["error_message"] = "Service unavailable"
    elif mutation == "duplicate_index":
        result["eval_case_results"].append(result["eval_case_results"][0])
    elif mutation == "missing_case":
        result["eval_case_results"].pop()
    elif mutation == "empty_candidates":
        result["eval_case_results"][0]["response_candidate_results"] = []
    elif mutation == "summary_error":
        result["summary_metrics"][0]["num_cases_error"] = 1
    elif mutation == "missing_suite":
        del manifest["suites"]["response"]
    elif mutation == "cli_error":
        manifest["commands"][0]["exit_code"] = 1
    elif mutation == "trace_tamper":
        (run / entry["trace"]).write_text("{}")
    elif mutation == "missing_file":
        (run / entry["trace"]).unlink()
    path.write_text(json.dumps(result))
    entry["results_sha256"] = digest(path)
    (run / "manifest.json").write_text(json.dumps(manifest))
    assert not evaluate(run, policy)["passed"]


def test_duplicate_json_keys_rejected(tmp_path):
    from scripts.quality_gate import read

    path = tmp_path / "duplicate.json"
    path.write_text('{"score":0,"score":1}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        read(path)


def test_json_fences_are_not_json():
    case = {
        "prompt": {"parts": [{"text": "Only JSON"}]},
        "responses": [{"response": {"parts": [{"text": "```json\n{}\n```"}]}}],
        "agent_data": {"turns": [{"turn_index": 0, "events": []}]},
    }
    assert "invalid JSON response" in trace_checks(case, {"json_response": True})


@pytest.mark.parametrize('extra_error', [None, 'unavailable grade', 'incomplete summary', 'trace mismatch'])
def test_negative_control_requires_only_the_expected_score_failure(tmp_path, extra_error):
    from scripts.linux_verify import valid_wrong_reference
    run = make_run(tmp_path, suites={'response': ['final_response_match']}, count=1,
                   low_metrics=['final_response_match'])
    manifest = json.loads((run / 'manifest.json').read_text())
    gate = evaluate(run, run / 'policy.json')
    if extra_error:
        gate['errors'].append(extra_error)
    assert valid_wrong_reference(manifest, gate) is (extra_error is None)
