"""#391: 실제 모델 없이 오차·회수율·범위 제한·캡처 변조를 검증한다."""

from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from evaluation.embedding.comparison import (
    EvaluationError, compare, digest, load_json, synthetic_capture, validate_capture, validate_dataset,
)
from scripts.compare_embedding_captures import main

DATASET = Path(__file__).resolve().parents[2] / 'evaluation/embedding/cases.json'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('임베딩 평가에서 네트워크 사용 금지')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


@pytest.fixture
def data():
    return validate_dataset(load_json(DATASET))


def set_settings(capture, **changes):
    capture['settings'].update(changes)
    capture['embedding_fingerprint'] = digest(capture['settings'])


def measured(capture, runtime):
    capture = deepcopy(capture)
    capture['synthetic'] = False
    set_settings(capture, runtime=runtime)
    # 유효성 검사 전용 가짜 실측 레코드. 파일로 저장·실측 결과로 게시하지 않는다.
    capture['measurements'] = dict(cold_ms=[100, 110], warm_ms=[10, 11], peak_rss_bytes=1024,
                                   environment='test-machine', procedure='fresh-process-v1',
                                   rss_method='process-peak', batch_policy='all-texts-batch-1',
                                   threads='1', device='cpu')
    return capture


def test_fixed_dataset_and_smoke_are_not_quality_or_acceptance(data):
    assert len(data['queries']) == 12
    assert len(data['chunks']) == 17
    capture = synthetic_capture(data)
    report = compare(data, capture, capture)
    assert report['quality_measurement'] is False
    assert report['performance_comparable'] is False
    assert report['decision'] == 'pending_thresholds'
    for group in ('chunks', 'queries'):
        assert report['vector_metrics'][group]['max_absolute_error'] == 0
    assert all(row['top_k_overlap'] == 1 for row in report['queries'])
    encoded = json.dumps(report)
    assert data['queries'][0]['text'] not in encoded
    assert '마지막 표식' not in encoded


def test_filter_before_top_k_excludes_highest_scoring_foreign_evidence(data):
    capture = synthetic_capture(data)
    # 불허 청크를 완벽 일치로 만들어도 순위에 들어가면 안 된다.
    query = next(q for q in data['queries'] if q['id'] == 'section-filter')
    forbidden = {'other-user', 'old-revision', 'unpublished', 'old-translation',
                 'other-version', 'outside-page', 'outside-section', 'continuation-tail'}
    for key in forbidden:
        capture['chunks'][key] = capture['queries'][query['id']][:]
    row = next(r for r in compare(data, capture, capture)['queries'] if r['query_id'] == query['id'])
    assert row['eligible_chunks'] == 4
    assert not forbidden & set(row['candidate_top_k'])


def test_cross_page_and_strict_page_have_distinct_eligible_sets(data):
    capture = synthetic_capture(data)
    report = {r['query_id']: r for r in compare(data, capture, capture)['queries']}
    assert report['cross-page']['eligible_chunks'] == 5
    assert report['cross-page']['candidate_required_recall'] == 1
    assert 'continuation-tail' not in report['strict-page']['candidate_top_k']
    assert report['strict-page']['eligible_chunks'] == 4


def test_known_vectors_detect_recall_loss_rank_changes_and_both_vector_groups(data):
    data = deepcopy(data)
    data['queries'] = [next(q for q in data['queries'] if q['id'] == 'near-scores')]
    baseline = synthetic_capture(data)
    set_settings(baseline, dimension=2)
    baseline['chunks'] = {c['id']: [0.0, 1.0] for c in data['chunks']}
    baseline['queries'] = {'near-scores': [1.0, 0.0]}
    baseline['chunks']['near-b'] = [1.0, 0.0]
    candidate = deepcopy(baseline)
    candidate['chunks']['near-a'] = [1.0, 0.0]
    # 완전 동점은 ID 오름차순으로 판정해 순서 변동을 숨기지 않는다.
    candidate['queries']['near-scores'] = [0.8, 0.6]
    report = compare(data, baseline, candidate)
    row = report['queries'][0]
    assert row['baseline_top_k'] == ['near-b']
    assert row['candidate_top_k'] == ['near-a']
    assert row['baseline_required_recall'] == 1
    assert row['candidate_required_recall'] == 0
    assert row['required_rank_changes'] == {'near-b': 1}
    assert row['candidate_cutoff_margin'] == 0
    assert report['vector_metrics']['chunks']['max_absolute_error'] == 1
    assert report['vector_metrics']['queries']['max_absolute_error'] == 0.6


@pytest.mark.parametrize('change,code', [
    ('dimension', 'DIMENSION_INVALID'), ('missing', 'VECTOR_IDS_INVALID'),
    ('extra', 'VECTOR_IDS_INVALID'), ('nan', 'VECTOR_NON_FINITE'),
    ('inf', 'VECTOR_NON_FINITE'), ('bool', 'VECTOR_NON_FINITE'), ('zero', 'VECTOR_NORM_INVALID'),
    ('normalization', 'NORMALIZATION_INVALID'), ('dataset', 'DATASET_MISMATCH'),
    ('fingerprint', 'FINGERPRINT_MISMATCH'), ('measurement', 'SYNTHETIC_MEASUREMENT_INVALID'),
])
def test_invalid_captures_fail_without_text(data, change, code):
    capture = synthetic_capture(data)
    key = data['chunks'][0]['id']
    vector = capture['chunks'][key]
    if change == 'dimension': vector.pop()
    elif change == 'missing': del capture['chunks'][key]
    elif change == 'extra': capture['chunks']['unknown'] = vector[:]
    elif change in ('nan', 'inf'): vector[0] = float(change)
    elif change == 'bool': vector[0] = True
    elif change == 'zero': capture['chunks'][key] = [0] * 8
    elif change == 'normalization': capture['chunks'][key] = [2] * 8
    elif change == 'dataset': capture['dataset_sha256'] = 'stale'
    elif change == 'fingerprint': capture['settings']['runtime'] = 'other'
    else: capture['measurements'] = {'secret': 'not-printed'}
    if change == 'fingerprint': code = 'RUNTIME_INVALID'
    with pytest.raises(EvaluationError, match=f'^{code}$'):
        compare(data, synthetic_capture(data), capture)


@pytest.mark.parametrize('field,value', [('model_revision', 'other'), ('tokenizer_revision', 'other'),
                                        ('pooling', 'other'), ('max_length', 256)])
def test_comparison_refuses_model_contract_changes(data, field, value):
    baseline = synthetic_capture(data)
    candidate = deepcopy(baseline)
    set_settings(candidate, **{field: value})
    with pytest.raises(EvaluationError, match='MODEL_CONTRACT_MISMATCH'):
        compare(data, baseline, candidate)


def test_runtime_precision_differences_are_recorded_not_silently_mixed(data):
    baseline = measured(synthetic_capture(data), 'pytorch')
    candidate = measured(synthetic_capture(data), 'onnx')
    set_settings(candidate, precision='int8', artifact_sha256='a' * 64)
    report = compare(data, baseline, candidate)
    assert report['quality_measurement'] is True
    assert report['performance_comparable'] is True
    assert report['baseline_fingerprint'] != report['candidate_fingerprint']
    assert report['candidate_settings']['precision'] == 'int8'
    candidate['measurements']['device'] = 'different-cpu'
    assert compare(data, baseline, candidate)['performance_comparable'] is False


@pytest.mark.parametrize('change', ['absent', 'negative', 'nan', 'missing-method', 'empty'])
def test_real_capture_requires_measurement_provenance(data, change):
    capture = measured(synthetic_capture(data), 'pytorch')
    if change == 'absent': capture['measurements'] = None
    elif change == 'negative': capture['measurements']['cold_ms'] = [-1]
    elif change == 'nan': capture['measurements']['peak_rss_bytes'] = float('nan')
    elif change == 'missing-method': del capture['measurements']['rss_method']
    else: capture['measurements']['warm_ms'] = []
    with pytest.raises(EvaluationError):
        validate_capture(capture, data)


def test_fixture_mutation_invalidates_capture_and_bad_required_evidence_is_refused(data):
    capture = synthetic_capture(data)
    changed = deepcopy(data)
    changed['queries'][0]['text'] += ' changed'
    with pytest.raises(EvaluationError, match='DATASET_MISMATCH'):
        compare(changed, capture, capture)
    changed['queries'][0]['required_chunk_ids'] = ['other-user']
    with pytest.raises(EvaluationError, match='REQUIRED_EVIDENCE_INVALID'):
        validate_dataset(changed)


@pytest.mark.parametrize('content', ['{"id":1,"id":2}', '{"value":NaN}'])
def test_duplicate_keys_and_nonfinite_json_are_refused(tmp_path, content):
    target = tmp_path / 'bad.json'
    target.write_text(content, encoding='utf-8')
    with pytest.raises(EvaluationError):
        load_json(target)


def test_cli_smoke_and_existing_file_protection(tmp_path, capsys):
    target = tmp_path / 'report.json'
    assert main(['--smoke', '--output', str(target)]) == 0
    original = target.read_bytes()
    assert json.loads(original)['quality_measurement'] is False
    assert main(['--smoke', '--output', str(target)]) == 2
    assert target.read_bytes() == original
    assert 'INPUT_OR_OUTPUT_INVALID' in capsys.readouterr().err


def test_cli_comparison_never_modifies_capture_or_leaks_invalid_body(data, tmp_path, capsys):
    capture = synthetic_capture(data)
    source = tmp_path / 'capture.json'
    source.write_text(json.dumps(capture), encoding='utf-8')
    original = source.read_bytes()
    args = ['--baseline', str(source), '--candidate', str(source)]
    assert main(args + ['--output', str(tmp_path / 'report.json')]) == 0
    assert source.read_bytes() == original
    source.write_text('{"private-body": "do-not-print"}', encoding='utf-8')
    assert main(args + ['--output', str(tmp_path / 'invalid.json')]) == 2
    assert 'do-not-print' not in capsys.readouterr().err
    assert not (tmp_path / 'invalid.json').exists()


def test_windows_non_utf8_console_still_returns_success(tmp_path):
    import os
    env = dict(os.environ, PYTHONIOENCODING='cp1252')
    script = DATASET.parents[2] / 'scripts/compare_embedding_captures.py'
    output = tmp_path / 'report.json'
    result = subprocess.run([sys.executable, str(script), '--smoke', '--output', str(output)],
                            env=env, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode('utf-8')
    assert '합성 smoke' in result.stdout.decode('utf-8')
    assert json.loads(output.read_text(encoding='utf-8'))['quality_measurement'] is False
