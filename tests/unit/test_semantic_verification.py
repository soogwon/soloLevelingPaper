"""의미 판정 정책을 고정 응답으로 검증한다. 실제 모델 정확도 평가는 별도다."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from solo_leveling.application.evidence_qa.semantic import SemanticVerificationService, SemanticVerificationError
from solo_leveling.application.evidence_qa.serialization import serialize_answer_response
from solo_leveling.domain.semantic_verification import (
    ClaimJudgment, SemanticVerdict as V, SemanticReason as R, VerificationFailure as F,
)
from solo_leveling.domain.evidence_qa import AnswerStatus, ReasonCode, ClaimSupport, GeneratedClaim, GeneratedAnswerDraft
from solo_leveling.infrastructure.generation.fake_semantic_verifier import FakeSemanticVerifier
from tests.unit.test_answer_service import parts


def configure(parts, outcomes):
    service, search, saved, requests = parts
    verifier = FakeSemanticVerifier(outcomes)
    writer = Mock(spec=['save'])
    service.evidence_writer = writer
    service.semantic_verification = SemanticVerificationService(verifier)
    return service, verifier, writer


def test_all_supported_and_serialization(parts):
    service, verifier, writer = configure(parts, [(V.SUPPORTED, R.FULL_SUPPORT)] * 2)
    response = service.answer('질문', context_id='ctx')
    assert response.result.status == AnswerStatus.OK
    assert serialize_answer_response(response)['verification_level'] == 'model_checked'
    assert len(verifier.calls) == 1
    writer.save.assert_called_once()
    assert {e.evidence_id for e in writer.save.call_args.args[1]} == {
        c.evidence_id for c in response.result.citations}


@pytest.mark.parametrize('outcome', [
    (V.PARTIALLY_SUPPORTED, R.MISSING_CONDITION),
    (V.PARTIALLY_SUPPORTED, R.UNSUPPORTED_DIRECTION),
    (V.PARTIALLY_SUPPORTED, R.UNSUPPORTED_CAUSALITY),
    (V.PARTIALLY_SUPPORTED, R.METRIC_CONFUSION),
    (V.PARTIALLY_SUPPORTED, R.PARTIAL_SUPPORT),
    (V.CONTRADICTED, R.CONTRADICTION),
    (V.UNDETERMINED, R.INSUFFICIENT_SUPPORT),
    (V.UNDETERMINED, R.DAMAGED_EVIDENCE),
])
def test_unsupported_claim_and_unused_evidence_are_not_saved(parts, outcome):
    service, verifier, writer = configure(parts, [(V.SUPPORTED, R.FULL_SUPPORT), outcome])
    response = service.answer('질문', context_id='ctx')
    assert response.result.status == AnswerStatus.PARTIAL
    assert response.result.reason_code == ReasonCode.SEMANTIC_LIMITED
    assert len(response.result.claims) == len(response.result.citations) == 1
    assert '한국어 근거 1' not in response.result.answer_ko
    writer.save.assert_called_once()
    assert len(writer.save.call_args.args[1]) == 1
    assert writer.save.call_args.args[1][0].evidence_id == response.result.citations[0].evidence_id
    assert len(verifier.calls) == 1


def test_all_excluded_no_claims_or_new_search(parts):
    service, verifier, writer = configure(parts, [(V.UNDETERMINED, R.INSUFFICIENT_SUPPORT)] * 2)
    response = service.answer('질문', context_id='ctx', top_k=2)
    assert response.result.status == AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.result.reason_code == ReasonCode.SEMANTIC_LIMITED
    assert not response.result.claims and not response.result.citations
    assert len(parts[3]) == len(verifier.calls) == 1
    writer.save.assert_not_called()


@pytest.mark.parametrize('failure', list(F))
def test_verification_failure_fails_closed(parts, failure):
    service, verifier, writer = configure(parts, [])
    verifier.verify = Mock(side_effect=SemanticVerificationError(failure))
    response = service.answer('질문', context_id='ctx')
    assert not response.result.claims
    assert response.result.reason_code == ReasonCode.VERIFICATION_FAILED
    assert serialize_answer_response(response)['verification_level'] == 'structural_only'
    assert '근거 부재를 뜻하지 않습니다' in response.result.answer_ko
    writer.save.assert_not_called()
    verifier.verify.assert_called_once()


@pytest.mark.parametrize('malformation', ['missing', 'duplicate', 'unknown', 'inconsistent', 'string_enum'])
def test_invalid_judgments_fail_closed(parts, malformation):
    service, verifier, writer = configure(parts, [])
    def verify(claims, evidence):
        rows = [ClaimJudgment(c.claim_id, V.SUPPORTED, R.FULL_SUPPORT) for c in claims]
        if malformation == 'missing':
            rows.pop()
        elif malformation == 'duplicate':
            rows[1] = rows[0]
        elif malformation == 'unknown':
            rows[1] = replace(rows[1], claim_id='invented')
        elif malformation == 'inconsistent':
            rows[1] = replace(rows[1], reason=R.CONTRADICTION)
        else:
            rows[1] = replace(rows[1], verdict='supported')
        return tuple(rows)
    verifier.verify = verify
    response = service.answer('질문', context_id='ctx')
    assert response.result.reason_code == ReasonCode.VERIFICATION_FAILED
    writer.save.assert_not_called()


@pytest.mark.parametrize('limits', [{'max_claims': 1}, {'max_input_chars': 1}])
def test_limits_do_not_call_provider_or_truncate(parts, limits):
    service, verifier, writer = configure(parts, [(V.SUPPORTED, R.FULL_SUPPORT)] * 2)
    service.semantic_verification = SemanticVerificationService(verifier, **limits)
    response = service.answer('질문', context_id='ctx')
    assert not verifier.calls
    assert response.result.reason_code == ReasonCode.VERIFICATION_FAILED
    writer.save.assert_not_called()


def test_existing_extraction_warning_is_not_upgraded(parts):
    service, verifier, writer = configure(parts, [(V.SUPPORTED, R.FULL_SUPPORT)])
    search = parts[1]
    broken = replace(search.items[0].chunk, original_text='Faster when the sequence\n6')
    selected = replace(search, items=(replace(search.items[0], chunk=broken),))
    service.search.search = lambda *a, **kw: SimpleNamespace(context_id='ctx', result=selected)
    service.generator.generate_claims = lambda q, ev: GeneratedAnswerDraft((
        GeneratedClaim('더 빠르다.', (ev[0].evidence_id,),
                       (ClaimSupport(ev[0].evidence_id, ev[0].original_text),)),))
    response = service.answer('조건은?', context_id='ctx')
    assert response.result.status == AnswerStatus.PARTIAL
    assert response.result.reason_code == ReasonCode.EXTRACTION_LIMITED


def test_only_cited_evidence_is_sent_and_multiple_citations_kept(parts):
    service, verifier, writer = configure(parts, [(V.SUPPORTED, R.FULL_SUPPORT)])
    service.generator.generate_claims = lambda q, ev: GeneratedAnswerDraft((
        GeneratedClaim('복합 주장', tuple(e.evidence_id for e in ev)),))
    response = service.answer('질문', context_id='ctx')
    assert len(verifier.calls[0][1]) == 2
    assert len(response.result.claims[0].evidence_ids) == 2
    service.generator.generate_claims = lambda q, ev: GeneratedAnswerDraft((
        GeneratedClaim('단일 주장', (ev[0].evidence_id,)),))
    service.answer('질문', context_id='ctx')
    assert len(verifier.calls[1][1]) == 1


def test_atomic_save_failure_is_not_success(parts):
    service, verifier, writer = configure(parts, [(V.SUPPORTED, R.FULL_SUPPORT)] * 2)
    writer.save.side_effect = OSError('DB failure')
    with pytest.raises(OSError):
        service.answer('질문', context_id='ctx')


def test_empty_draft_does_not_call_verifier(parts):
    service, verifier, writer = configure(parts, [])
    service.generator.generate_claims = lambda *a: GeneratedAnswerDraft(())
    service.answer('질문', context_id='ctx')
    assert not verifier.calls
    writer.save.assert_not_called()


def test_diagnostics_only_emit_ids_and_safe_codes(parts, capsys):
    import json
    service, _, writer = configure(parts, [(V.SUPPORTED, R.FULL_SUPPORT)] * 2)
    service.answer('비공개 질문', context_id='ctx')
    captured = capsys.readouterr()
    assert captured.out == ''
    assert '비공개 질문' not in captured.err and '한국어 근거' not in captured.err
    records = [json.loads(line) for line in captured.err.splitlines()]
    judgments = [r for r in records if r.get('check_item') == 'claim.verdict']
    assert len(judgments) == 2
    assert len({r['span_id'] for r in judgments}) == 1
    assert not any(r.get('event') == 'judgment' for r in records)


def test_verification_failure_survives_default_safe_filter(parts, capsys):
    import json
    from io import StringIO
    from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
    service, verifier, writer = configure(parts, [])
    verifier.verify = Mock(side_effect=SemanticVerificationError(F.TIMEOUT))
    service.answer('비공개 질문', context_id='ctx')
    output = StringIO()
    SafeDiagnosticStream(output).write(capsys.readouterr().err)
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert any(r['stage'] == 'semantic_verification' and r['event'] == 'error'
               and r['error_code'] == 'STAGE_FAILED' for r in records)
    assert '비공개 질문' not in output.getvalue()
    writer.save.assert_not_called()


@pytest.mark.parametrize('original,claim,outcome', [
    ('Performance depends on path length.', '경로가 짧을수록 성능이 좋다.',
     (V.PARTIALLY_SUPPORTED, R.UNSUPPORTED_DIRECTION)),
    ('Attention is faster when n is smaller than d.', 'Attention은 항상 더 빠르다.',
     (V.PARTIALLY_SUPPORTED, R.MISSING_CONDITION)),
    ('Attention requires a constant number of sequential operations.', '총계산량은 항상 더 적다.',
     (V.PARTIALLY_SUPPORTED, R.METRIC_CONFUSION)),
    ('The study found an association between A and B.', 'A가 B를 일으킨다.',
     (V.PARTIALLY_SUPPORTED, R.UNSUPPORTED_CAUSALITY)),
    ('The method reduces memory use.', '메모리와 실행 시간이 모두 줄어든다.',
     (V.PARTIALLY_SUPPORTED, R.PARTIAL_SUPPORT)),
    ('The method increases memory use.', '메모리 사용량이 줄어든다.',
     (V.CONTRADICTED, R.CONTRADICTION)),
    ('Attention is faster when n is smaller than d.', 'n이 d보다 작으면 Attention이 더 빠르다.',
     (V.SUPPORTED, R.FULL_SUPPORT)),
])
def test_regression_examples_follow_scripted_verdict(parts, original, claim, outcome):
    # 사례별 판정은 고정한다. 이 테스트는 실제 모델의 의미 이해 정확도를 보증하지 않는다.
    service, verifier, writer = configure(parts, [outcome])
    search = parts[1]
    chunk = replace(search.items[0].chunk, original_text=original)
    selected = replace(search, items=(replace(search.items[0], chunk=chunk),))
    service.search.search = lambda *a, **kw: SimpleNamespace(context_id='ctx', result=selected)
    service.generator.generate_claims = lambda q, ev: GeneratedAnswerDraft((
        GeneratedClaim(claim, (ev[0].evidence_id,),
                       (ClaimSupport(ev[0].evidence_id, original),)),))
    response = service.answer('질문', context_id='ctx')
    assert verifier.calls[0][0][0].text == claim
    assert verifier.calls[0][1][0].original_text == original
    if outcome[0] == V.SUPPORTED:
        assert response.result.claims[0].text == claim
        writer.save.assert_called_once()
    else:
        assert not response.result.claims
        assert response.result.reason_code == ReasonCode.SEMANTIC_LIMITED
        writer.save.assert_not_called()
