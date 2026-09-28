"""답변 생성의 구조 검증과 저장 순서를 검증한다."""

from types import SimpleNamespace

import pytest

from solo_leveling.application.evidence_qa.answer import AnswerService, AnswerGenerationError
from solo_leveling.application.evidence_qa.entry import ContextSearchResponse
from solo_leveling.application.evidence_qa.ports import GenerationUnavailable
from solo_leveling.application.evidence_qa.serialization import serialize_answer_response
from solo_leveling.domain.evidence_qa import (
    AnswerStatus, GeneratedAnswerDraft, GeneratedClaim, ReasonCode, RetrievedChunk,
    RetrievalMethod, SearchResult, SearchScope,
)
from solo_leveling.domain.models import Chunk
from solo_leveling.infrastructure.generation.fake_generator import FakeClaimGenerator


@pytest.fixture
def parts():
    result = SearchResult('질문', SearchScope('v', 'p', 't'), RetrievalMethod.VECTOR, 'idx', tuple(
        RetrievedChunk(Chunk(f'c{i}', 'p', i, f'Original {i}', f'한국어 근거 {i}', 't', pdf_page=i+1), 1., i+1)
        for i in range(2)))
    saved, requests = [], []

    def search(question, **kwargs):
        requests.append(kwargs)
        return ContextSearchResponse('ctx', result)

    writer = SimpleNamespace(save=lambda context, evidence: saved.append((context, evidence)))
    service = AnswerService(SimpleNamespace(search=search), FakeClaimGenerator(), writer)
    return service, result, saved, requests


def test_normal_answer_and_explicit_verification_level(parts):
    service, _, saved, requests = parts
    response = service.answer('질문', version_id='v', pdf_pages=(1, 2))
    assert requests[0]['version_id'] == 'v'
    assert response.result.status == AnswerStatus.OK
    assert len(response.result.claims) == len(response.result.citations) == 2
    assert response.result.answer_ko == '한국어 근거 0\n한국어 근거 1'
    assert saved[0][0] == 'ctx'
    payload = serialize_answer_response(response)
    assert payload['verification_level'] == 'structural_only'
    assert payload['context_id'] == 'ctx'
    assert 'search' not in payload


@pytest.mark.parametrize('case', ['unknown', 'empty_refs', 'duplicate_refs', 'wrong_type'])
def test_invalid_claim_references_save_nothing(parts, case):
    service, _, saved, _ = parts

    def generate(question, evidence):
        if case == 'wrong_type':
            return {'claims': []}
        eid = evidence[0].evidence_id
        ids = {'unknown': ('invented',), 'empty_refs': (), 'duplicate_refs': (eid, eid)}[case]
        return GeneratedAnswerDraft((GeneratedClaim('주장', ids),))

    service.generator = SimpleNamespace(generate_claims=generate)
    result = service.answer('질문', context_id='ctx').result
    assert result.status == AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.reason_code == ReasonCode.VERIFICATION_FAILED
    assert result.claims == result.citations == ()
    assert not saved


def test_empty_search_skips_generator_and_save(parts):
    service, result, saved, _ = parts
    from dataclasses import replace
    service.search = SimpleNamespace(search=lambda *a, **kw: ContextSearchResponse('ctx', replace(result, items=())))
    service.generator = SimpleNamespace(generate_claims=lambda *a: pytest.fail('생성기 호출 금지'))
    assert service.answer('질문', context_id='ctx').result.reason_code == ReasonCode.EVIDENCE_NOT_FOUND
    assert not saved


def test_empty_draft_saves_nothing(parts):
    service, _, saved, _ = parts
    service.generator = SimpleNamespace(generate_claims=lambda *a: GeneratedAnswerDraft(()))
    assert service.answer('질문', context_id='ctx').result.reason_code == ReasonCode.EVIDENCE_NOT_FOUND
    assert not saved


def test_provider_failure_is_not_insufficient_evidence(parts):
    service, _, saved, _ = parts

    def unavailable(*args):
        raise GenerationUnavailable('비공개 제공자 오류')

    service.generator = SimpleNamespace(generate_claims=unavailable)
    with pytest.raises(AnswerGenerationError) as caught:
        service.answer('질문', context_id='ctx')
    assert '비공개' not in str(caught.value)
    assert not saved


def test_save_failure_prevents_success(parts):
    service, _, _, _ = parts

    def fail(*args):
        raise RuntimeError('저장 실패')

    service.evidence_writer = SimpleNamespace(save=fail)
    with pytest.raises(RuntimeError, match='저장 실패'):
        service.answer('질문', context_id='ctx')


def test_only_used_evidence_is_saved_once(parts):
    service, _, saved, _ = parts
    service.generator = SimpleNamespace(generate_claims=lambda q, ev: GeneratedAnswerDraft((
        GeneratedClaim('첫째 주장', (ev[1].evidence_id,)), GeneratedClaim('둘째 주장', (ev[1].evidence_id,)))))
    response = service.answer('질문', context_id='ctx')
    assert len(response.result.claims) == 2
    assert len(response.result.citations) == len(saved[0][1]) == 1
    assert saved[0][1][0].chunk_id == 'c1'


def test_duplicate_generated_ids_fail_before_saving(parts):
    service, _, saved, _ = parts
    service.id_factory = lambda: 'same'
    with pytest.raises(ValueError, match='ID가 중복'):
        service.answer('질문', context_id='ctx')
    assert not saved


def test_parser_failure_is_verification_failure_and_saves_nothing(parts):
    from solo_leveling.application.evidence_qa.response_parser import parse_generated_answer
    service, _, saved, _ = parts
    service.generator = SimpleNamespace(generate_claims=lambda *args: parse_generated_answer('{'))
    result = service.answer('질문', context_id='ctx').result
    assert result.reason_code == ReasonCode.VERIFICATION_FAILED
    assert result.claims == result.citations == ()
    assert not saved


@pytest.mark.parametrize('case', ['valid', 'empty', 'unknown', 'duplicate'])
def test_parsed_response_passes_through_reference_checks(parts, case):
    import json
    from solo_leveling.application.evidence_qa.response_parser import parse_generated_answer
    service, _, saved, _ = parts

    def generate(question, evidence):
        eid = evidence[0].evidence_id
        ids = {'valid': [eid], 'empty': [], 'unknown': ['unknown'], 'duplicate': [eid, eid]}[case]
        return parse_generated_answer(json.dumps({'claims': [
            {'text': '주장', 'evidence_ids': ids},
        ]}))

    service.generator = SimpleNamespace(generate_claims=generate)
    result = service.answer('질문', context_id='ctx').result
    if case == 'valid':
        assert result.status == AnswerStatus.OK
        assert len(saved) == 1
    else:
        assert result.reason_code == ReasonCode.VERIFICATION_FAILED
        assert not saved
