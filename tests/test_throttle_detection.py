"""Quota cooldown must not react to ordinary candidate/reference data or scores."""
import pytest
from adk_eval_harness.runner import observed_throttle, save


@pytest.mark.parametrize('message', [
    'Retryable error (code=429) on attempt 1/5',
    '429 RESOURCE_EXHAUSTED. Judge model capacity',
    'HTTP 429 Too Many Requests',
])
def test_real_transport_error_signals_cooldown(tmp_path, message):
    log = tmp_path / 'grade.log'
    log.write_text(message)
    assert observed_throttle(log, tmp_path)


def test_trace_text_and_fractional_score_do_not_signal_cooldown(tmp_path):
    log = tmp_path / 'grade.log'
    log.write_text('score 0.429 for load-00429\n')
    save(tmp_path / 'results_fixture.json', {
        'evaluation_dataset': [{'prompt': 'Explain RESOURCE_EXHAUSTED and 429'}],
        'eval_case_results': [{'response_candidate_results': [{'metric_results': {
            'metric': {'score': .429, 'explanation': 'The answer correctly explains 429 RESOURCE_EXHAUSTED'}}}]}]})
    assert not observed_throttle(log, tmp_path)


def test_native_error_signals_cooldown_without_sdk_log(tmp_path):
    save(tmp_path / 'results_fixture.json', {'eval_case_results': [
        {'response_candidate_results': [{'metric_results': {
            'metric': {'score': None, 'error_message': 'RESOURCE_EXHAUSTED'}}}]}]})
    assert observed_throttle(tmp_path / 'missing.log', tmp_path)
