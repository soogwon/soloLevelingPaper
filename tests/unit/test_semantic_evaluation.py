"""모델 정답률이 아닌 평가 실행·집계·정답 격리를 검증한다."""

from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from evaluation.semantic.runner import DatasetError, OfflineVerifier, evaluate, load_dataset
from solo_leveling.application.evidence_qa.semantic import SemanticVerificationError
from solo_leveling.application.evidence_qa.semantic import verification_payload
from solo_leveling.domain.semantic_verification import (
    ClaimJudgment, SemanticVerdict as V, SemanticReason as R,
    VerificationFailure as F, VerificationDetail as D,
)

DATA = Path(__file__).resolve().parents[2] / 'evaluation/semantic/cases.json'


@pytest.fixture
def dataset():
    return load_dataset(DATA)


def subset(data, *ids):
    result = deepcopy(data)
    result['cases'] = [c for c in result['cases'] if c['id'] in ids]
    return result


class SequenceVerifier:
    model = 'scripted-offline'
    prompt_version = 'test-v1'

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def verify(self, claims, evidence):
        self.calls.append((claims, evidence))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        verdict, reason = outcome
        return (ClaimJudgment(claims[0].claim_id, verdict, reason),)


def test_dataset_has_reviewable_cases_and_group_separation(dataset):
    data, digest = dataset
    assert len(digest) == 64
    assert len(data['cases']) == 24
    assert {c['expected']['verdict'] for c in data['cases']} == {v.value for v in V}
    assert all(c['review_status'] == 'draft' for c in data['cases'])
    dev = {c['group'] for c in data['cases'] if c['split'] == 'development'}
    held = {c['group'] for c in data['cases'] if c['split'] == 'holdout'}
    assert not dev & held


@pytest.mark.parametrize('mutation', [
    lambda d: d['cases'].append(deepcopy(d['cases'][0])),
    lambda d: d['cases'][0]['claim'].update(evidence_ids=['missing']),
    lambda d: d['cases'][0]['evidence'].append(deepcopy(d['cases'][0]['evidence'][0])),
    lambda d: d['cases'][0]['expected'].update(reasons=['contradiction']),
    lambda d: d['cases'][0]['evidence'][0].update(pdf_page=True),
    lambda d: d['cases'][0]['evidence'][0].update(follows_evidence_id='e1'),
    lambda d: d['cases'][0].update(split='holdout'),
    lambda d: d['cases'][0].update(review_status='unknown'),
    lambda d: d.update(schema_version=True),
])
def test_invalid_dataset_is_rejected_without_body(dataset, tmp_path, mutation):
    data, _ = dataset
    mutation(data)
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(DatasetError, match='^EVALUATION_DATASET_INVALID$'):
        load_dataset(path)


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / 'cases.json'
    path.write_text('{"schema_version":1,"schema_version":1}', encoding='utf-8')
    with pytest.raises(DatasetError):
        load_dataset(path)


def test_counts_distinguish_wrong_approval_wrong_rejection_and_failures(dataset):
    data, digest = dataset
    data = subset(data, 'temperature-normal', 'temperature-error', 'direction-normal',
                  'direction-error', 'causality-normal')
    verifier = SequenceVerifier([
        (V.SUPPORTED, R.FULL_SUPPORT),
        (V.SUPPORTED, R.FULL_SUPPORT),
        (V.UNDETERMINED, R.INSUFFICIENT_SUPPORT),
        (V.PARTIALLY_SUPPORTED, R.UNSUPPORTED_DIRECTION),
        SemanticVerificationError(F.TIMEOUT),
    ])
    result = evaluate(data, digest, verifier)
    summary = result['summary']
    assert summary['attempts'] == 5
    assert summary['scored_completed'] == 4
    assert summary['failures'] == {'timeout': 1}
    assert summary['false_support'] == {'count': 1, 'denominator': 2, 'rate': .5}
    assert summary['supported_not_approved'] == {'count': 1, 'denominator': 2, 'rate': .5}
    assert summary['verdict_match']['count'] == 2
    assert summary['undetermined'] == 1
    assert sum(sum(row.values()) for row in summary['confusion_matrix'].values()) == 4
    assert result['results'][-1]['verdict'] is None
    assert result['token_usage'] is None and result['estimated_cost'] is None


def test_reason_mismatch_does_not_change_verdict_accuracy(dataset):
    data, digest = dataset
    result = evaluate(subset(data, 'direction-error'), digest,
                      SequenceVerifier([(V.PARTIALLY_SUPPORTED, R.PARTIAL_SUPPORT)]))
    assert result['summary']['verdict_match']['rate'] == 1
    assert result['summary']['reason_match']['rate'] == 0


def test_expected_answers_never_reach_verifier(dataset):
    data, digest = dataset
    data = subset(data, 'crosspage-normal')
    data['cases'][0]['expected']['rationale'] = 'SECRET_EXPECTED_MARKER'
    verifier = SequenceVerifier([(V.SUPPORTED, R.FULL_SUPPORT)])
    result = evaluate(data, digest, verifier)
    claims, evidence = verifier.calls[0]
    assert not hasattr(claims[0], 'expected')
    assert 'SECRET_EXPECTED_MARKER' not in repr(verifier.calls)
    assert [e.pdf_page for e in evidence] == [6, 7]
    assert evidence[1].follows_evidence_id == evidence[0].evidence_id
    assert set(claims[0].evidence_ids) == {e.evidence_id for e in evidence}
    assert 'original_text' not in json.dumps(result)
    assert claims[0].text not in json.dumps(result, ensure_ascii=False)


def test_all_identifier_hints_removed_and_linkage_preserved(dataset):
    data, digest = dataset
    data = subset(data, 'crosspage-normal')
    case = data['cases'][0]
    case['id'] = 'SECRET_NORMAL_CASE'
    case['group'] = 'SECRET_GROUP'
    case['category'] = 'SECRET_CATEGORY'
    case['expected']['rationale'] = 'SECRET_RATIONALE'
    case['claim']['evidence_ids'] = ['SECRET_CONDITION', 'SECRET_SUPPORTED']
    case['evidence'][0]['id'] = 'SECRET_CONDITION'
    case['evidence'][1]['id'] = 'SECRET_SUPPORTED'
    case['evidence'][1]['follows_evidence_id'] = 'SECRET_CONDITION'
    verifier = SequenceVerifier([(V.SUPPORTED, R.FULL_SUPPORT)])
    result = evaluate(data, digest, verifier)
    claims, evidence = verifier.calls[0]
    assert 'SECRET_' not in repr(verifier.calls)
    assert 'SECRET_' not in json.dumps(verification_payload(claims, evidence))
    assert claims[0].claim_id == 'claim-1'
    assert claims[0].evidence_ids == ('evidence-1', 'evidence-2')
    assert [e.chunk_id for e in evidence] == ['chunk-1', 'chunk-2']
    assert evidence[1].follows_evidence_id == 'evidence-1'
    assert [e.pdf_page for e in evidence] == [6, 7]
    assert result['results'][0]['case_id'] == 'SECRET_NORMAL_CASE'
    assert result['summary']['verdict_match']['rate'] == 1


def test_reviewed_summary_excludes_drafts_and_ambiguous_but_keeps_failures(dataset):
    data, digest = dataset
    data = subset(data, 'temperature-normal', 'temperature-error', 'direction-normal', 'direction-error')
    for case, status in zip(data['cases'], ['reviewed', 'draft', 'reviewed', 'ambiguous']):
        case['review_status'] = status
    verifier = SequenceVerifier([
        (V.SUPPORTED, R.FULL_SUPPORT), (V.SUPPORTED, R.FULL_SUPPORT),
        SemanticVerificationError(F.TIMEOUT), (V.SUPPORTED, R.FULL_SUPPORT),
    ])
    result = evaluate(data, digest, verifier)
    assert result['summary']['attempts'] == 4
    assert result['summary']['verdict_match']['rate'] == .5
    reviewed = result['reviewed_summary']
    assert reviewed['attempts'] == reviewed['reviewed_attempts'] == 2
    assert reviewed['scored_completed'] == 1
    assert reviewed['verdict_match']['rate'] == 1
    assert reviewed['failures'] == {'timeout': 1}
    assert reviewed['draft_attempts'] == reviewed['ambiguous_attempts'] == 0
    assert result['summary_scope'] == 'provisional_including_drafts'


def test_split_summaries_keep_development_and_holdout_separate(dataset):
    data, digest = dataset
    result = evaluate(data, digest, OfflineVerifier(), split='all', repeats=2, max_calls=48)
    assert result['summary']['attempts'] == 48
    assert result['splits']['development']['summary']['attempts'] == 32
    assert result['splits']['holdout']['summary']['attempts'] == 16
    assert result['reviewed_summary']['attempts'] == 0
    assert result['reviewed_summary']['verdict_match']['rate'] is None
    for split in result['splits'].values():
        assert split['reviewed_summary']['scored_completed'] == 0
        assert split['reviewed_summary']['false_support']['rate'] is None
    dev_only = evaluate(data, digest, OfflineVerifier())
    assert dev_only['splits']['holdout']['summary']['attempts'] == 0
    assert dev_only['splits']['holdout']['summary']['verdict_match']['rate'] is None


def test_reviewed_cases_are_counted_only_in_their_split(dataset):
    data, digest = dataset
    data = subset(data, 'temperature-normal', 'dose-error')
    for case in data['cases']:
        case['review_status'] = 'reviewed'
    result = evaluate(data, digest, OfflineVerifier(), split='all')
    assert result['reviewed_summary']['attempts'] == 2
    assert result['splits']['development']['reviewed_summary']['verdict_match']['rate'] == 1
    assert result['splits']['holdout']['reviewed_summary']['verdict_match']['rate'] == 0


def test_call_limit_is_checked_before_any_execution(dataset):
    data, digest = dataset
    verifier = SequenceVerifier([])
    with pytest.raises(ValueError, match='EVALUATION_CALL_LIMIT'):
        evaluate(data, digest, verifier, repeats=2, max_calls=1)
    assert not verifier.calls


@pytest.mark.parametrize('kwargs', [{'repeats': 0}, {'repeats': True}, {'max_calls': False}, {'split': 'bad'}])
def test_bad_options_rejected(dataset, kwargs):
    data, digest = dataset
    with pytest.raises(ValueError, match='EVALUATION_OPTIONS_INVALID'):
        evaluate(data, digest, OfflineVerifier(), **kwargs)


def test_repeats_and_failures_are_not_retried(dataset):
    data, digest = dataset
    verifier = SequenceVerifier([
        (V.SUPPORTED, R.FULL_SUPPORT), SemanticVerificationError(F.UNAVAILABLE),
        (V.UNDETERMINED, R.INSUFFICIENT_SUPPORT),
    ])
    result = evaluate(subset(data, 'temperature-normal'), digest, verifier, repeats=3, max_calls=3)
    assert len(verifier.calls) == 3
    assert result['summary']['varying_case_ids'] == ['temperature-normal']


def test_malformed_response_records_safe_code(dataset):
    data, digest = dataset
    result = evaluate(subset(data, 'temperature-normal'), digest,
                      SequenceVerifier([(V.SUPPORTED, R.CONTRADICTION)]))
    row = result['results'][0]
    assert row['failure'] == 'invalid_response'
    assert row['detail_code'] == 'CLAIM_SET_INVALID'
    assert result['summary']['verdict_match']['rate'] is None


def test_check_location_preserved(dataset):
    data, digest = dataset
    error = SemanticVerificationError(F.INVALID_RESPONSE, detail=D.ORIGINAL_QUOTE_ID_UNKNOWN,
                                      check='original.conditions')
    result = evaluate(subset(data, 'temperature-normal'), digest, SequenceVerifier([error]))
    assert result['results'][0]['check'] == 'original.conditions'
    assert result['results'][0]['detail_code'] == 'ORIGINAL_QUOTE_ID_UNKNOWN'


def test_ambiguous_cases_not_scored(dataset):
    data, digest = dataset
    data = subset(data, 'temperature-normal')
    data['cases'][0]['review_status'] = 'ambiguous'
    result = evaluate(data, digest, OfflineVerifier())
    assert result['summary']['ambiguous_attempts'] == 1
    assert result['summary']['scored_completed'] == 0
    assert result['summary']['verdict_match']['rate'] is None


def test_offline_runs_without_network_and_does_not_copy_expectations(dataset, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('오프라인 평가가 네트워크를 사용했습니다.')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    data, digest = dataset
    result = evaluate(data, digest, OfflineVerifier(), split='all', max_calls=24)
    assert len(result['results']) == 24
    assert result['summary']['false_support']['count'] > 0
    assert result['summary']['verdict_match']['rate'] < 1
    assert all(row['verdict'] == 'supported' for row in result['results'])


def test_cli_has_no_live_mode_and_output_is_explicitly_offline(monkeypatch, tmp_path, capsys):
    from scripts import evaluate_semantic as cli
    # CLI 동작을 임시 저장소에서 재현해 실제 사용자 파일을 건드리지 않는다.
    real_root = cli.ROOT
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    paths = ['evaluation/semantic/cases.json', 'evaluation/semantic/runner.py',
             'src/solo_leveling/application/evidence_qa/semantic.py',
             'src/solo_leveling/domain/semantic_verification.py']
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((real_root / relative).read_bytes())
    monkeypatch.setattr(cli, '__file__', str(tmp_path / 'scripts/evaluate_semantic.py'))
    script = Path(cli.__file__)
    script.parent.mkdir(parents=True)
    script.write_text('# 테스트용 소스', encoding='utf-8')
    from types import SimpleNamespace
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='test-revision'))
    assert cli.main(['--split', 'all', '--max-calls', '24']) == 0
    result = json.loads(next((tmp_path / 'runtime/semantic-evaluation').glob('*.json')).read_text(encoding='utf-8'))
    assert result['mode'] == 'offline' and result['quality_measurement'] is False
    assert result['working_tree_dirty'] is True
    assert len(result['source_hashes']) == 4
    assert '실제 모델 품질 평가가 아닙니다' in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        cli.main(['--live'])
    assert exc.value.code == 2
