"""Synthetic complete/partial traces guard against partial-success reuse."""
from scripts.run_evals import complete_trace
from adk_eval_harness.runner import save
from tests.fixture_runs import case


def test_partial_success_is_not_reusable(tmp_path):
    trace = tmp_path / 'traces.json'
    save(trace, {'eval_cases': [case('one')]})
    assert not complete_trace(trace, {'cases': {'one': {}, 'missing': {}}})


def test_complete_multiturn_trace_is_reusable(tmp_path):
    trace = tmp_path / 'traces.json'
    save(trace, {'eval_cases': [case('one', turns=3)]})
    assert complete_trace(trace, {'cases': {'one': {'min_turns': 3}}})
    assert not complete_trace(trace, {'cases': {'one': {'min_turns': 4}}})


def test_missing_trace_is_not_reusable(tmp_path):
    assert not complete_trace(tmp_path / 'missing.json', {'cases': {'required': {}}})
