"""평가 조건 불일치와 초안 점수의 오해를 방지하는 비교 테스트."""

from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from evaluation.semantic.runner import load_dataset, evaluate, OfflineVerifier
from evaluation.semantic.comparison import compare_results, load_result


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('실제 네트워크 연결 금지')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


@pytest.fixture
def inputs():
    data, digest = load_dataset(Path(__file__).resolve().parents[2] / 'evaluation/semantic/cases.json')
    result = evaluate(data, digest, OfflineVerifier(), split='all', repeats=2, max_calls=48)
    result.update(mode='offline', quality_measurement=False)
    return result, data, digest


def test_same_result_has_no_changes_and_no_reviewed_scores(inputs):
    result, data, digest = inputs
    report = compare_results(result, result, data, digest)
    assert report['changed_case_ids'] == []
    assert report['summary']['baseline']['attempts'] == 48
    assert report['reviewed_summary']['baseline']['verdict_match']['rate'] is None
    assert report['splits']['holdout']['summary']['baseline']['attempts'] == 16


def test_failure_is_not_scored_as_wrong_or_undetermined(inputs):
    baseline, data, digest = inputs
    candidate = deepcopy(baseline)
    candidate['results'][0].update(failure='timeout', verdict=None, reason=None)
    candidate['summary'] = {'untrusted': 'ignore-me'}
    report = compare_results(baseline, candidate, data, digest)
    summary = report['summary']['candidate']
    assert summary['failures'] == {'timeout': 1}
    assert summary['scored_completed'] == 47
    assert report['changed_case_ids'] == [baseline['results'][0]['case_id']]
    assert 'ignore-me' not in json.dumps(report)


@pytest.mark.parametrize('field,value', [
    ('dataset_sha256', 'other'), ('dataset_version', 'other'), ('schema_version', 1),
    ('mode', 'provider'), ('repeats', 3), ('max_input_chars', 1), ('max_claims', 1),
])
def test_incompatible_conditions_are_rejected(inputs, field, value):
    baseline, data, digest = inputs
    candidate = deepcopy(baseline)
    candidate[field] = value
    with pytest.raises(ValueError, match='EVALUATION_COMPARISON_INVALID'):
        compare_results(baseline, candidate, data, digest)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'expected', 'review', 'nan', 'reason'])
def test_corrupt_rows_are_rejected(inputs, change):
    baseline, data, digest = inputs
    candidate = deepcopy(baseline)
    rows = candidate['results']
    if change == 'missing':
        rows.pop()
    elif change == 'duplicate':
        rows.append(deepcopy(rows[0]))
    elif change == 'expected':
        rows[0]['expected_verdict'] = 'undetermined'
    elif change == 'review':
        rows[0]['review_status'] = 'reviewed'
    elif change == 'nan':
        rows[0]['elapsed_ms'] = float('nan')
    else:
        rows[0]['reason'] = 'contradiction'
    with pytest.raises(ValueError, match='EVALUATION_COMPARISON_INVALID'):
        compare_results(baseline, candidate, data, digest)


def test_model_and_prompt_may_change_but_no_winner_is_declared(inputs):
    baseline, data, digest = inputs
    candidate = deepcopy(baseline)
    candidate.update(model='other-model', prompt_version='other-prompt')
    report = compare_results(baseline, candidate, data, digest)
    assert report['candidate']['model'] == 'other-model'
    assert 'winner' not in report


def test_duplicate_json_keys_are_rejected(tmp_path):
    target = tmp_path / 'invalid.json'
    target.write_text('{"mode":"offline","mode":"provider"}', encoding='utf-8')
    with pytest.raises(ValueError, match='EVALUATION_COMPARISON_INVALID'):
        load_result(target)


@pytest.mark.parametrize('field', ['timeout_seconds', 'max_output_tokens', 'transport_max_attempts'])
def test_provider_limits_must_match(inputs, field):
    baseline, data, digest = inputs
    baseline.update(mode='provider', quality_measurement=True, timeout_seconds=30,
                    max_output_tokens=2048, transport_max_attempts=1)
    candidate = deepcopy(baseline)
    candidate[field] *= 2
    with pytest.raises(ValueError, match='EVALUATION_COMPARISON_INVALID'):
        compare_results(baseline, candidate, data, digest)


def test_cli_writes_new_comparison_without_changing_inputs(inputs, tmp_path, monkeypatch):
    from scripts import compare_semantic_evaluations as cli
    result, _, _ = inputs
    target = tmp_path / 'result.json'
    target.write_text(json.dumps(result), encoding='utf-8')
    before = target.read_bytes()
    dataset = Path(__file__).resolve().parents[2] / 'evaluation/semantic/cases.json'
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    assert cli.main(['--baseline', str(target), '--candidate', str(target), '--dataset', str(dataset)]) == 0
    assert target.read_bytes() == before
    assert len(list((tmp_path / 'runtime/semantic-evaluation').glob('comparison-*.json'))) == 1
