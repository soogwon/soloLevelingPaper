"""실제 제공자 어댑터의 쌍 입력과 서버의 최종 연결 검사를 함께 검증한다."""

import json
from unittest.mock import Mock

import pytest
import requests

from solo_leveling.infrastructure.generation.openai_generator import (
    OpenAIClaimGenerator, OpenAIGenerationSettings,
)
from tests.unit.test_answer_service import parts, repair_parts
from tests.unit.test_openai_generator import envelope


@pytest.mark.parametrize('case', ['valid', 'missing_next', 'split', 'empty', 'invalid_source', 'missing_source'])
def test_pair_context_does_not_bypass_boundary_validation(repair_parts, monkeypatch, case):
    service, saved, lookup = repair_parts
    calls = []

    def post(url, **kwargs):
        data = json.loads(kwargs['json']['input'][0]['content'])
        calls.append(data)
        if len(calls) == 1:
            assert 'maxItems' not in kwargs['json']['text']['format']['schema']['properties']['claims']
            selected = data['evidence'][:1]
        else:
            schema = kwargs['json']['text']['format']['schema']['properties']['claims']
            assert 'maxItems' not in schema
            assert schema['items']['properties']['source_claim_number']['enum'] == [1]
            pair, = data['repair_contexts']
            option = next(o for o in data['repair_quote_options'] if len(o['quotes']) == 2)
            assert option['quotes'][0]['text'] == 'Faster when the sequence\n6'
            assert option['quotes'][1]['text'] == 'length n is less than d.'
            assert pair['claim_number'] == 1
            assert pair['draft_claim'] == data['draft']['claims'][0]
            selected = [pair['left']] if case == 'missing_next' else [pair['left'], pair['next']]
            assert data['question'] != '조건은?'
            assert all(set(o['source_claim_numbers']) == {1} for o in data['repair_quote_options'])
        body = {'claims': [{'text': '조건부 비교',
            'evidence_ids': [item['evidence_id'] for item in selected],
            'supports': [{'evidence_id': item['evidence_id'], 'quote_original': item.get('original_text', '사용하지 않는 임시 값')}
                         for item in selected]}]}
        if len(calls) == 2:
            option = next(o for o in data['repair_quote_options'] if len(o['quotes']) == 2)
            body = {'claims': [{'text': '조건부 비교', 'support_ids': [
                'unavailable-left-only' if case == 'missing_next' else option['selection_id']]}]}
        if len(calls) == 2 and case != 'missing_source':
            body['claims'][0]['source_claim_number'] = 2 if case == 'invalid_source' else 1
        if len(calls) == 2 and case == 'split':
            body['claims'].append({**body['claims'][0], 'text': '두 번째 조건부 사실'})
        if len(calls) == 2 and case == 'empty':
            body['claims'] = []
        response = Mock(status_code=200)
        response.json.return_value = envelope(json.dumps(body))
        return response

    monkeypatch.setattr(requests, 'post', post)
    service.generator = OpenAIClaimGenerator(OpenAIGenerationSettings('test-only', allow_external_api=True))
    response = service.answer('조건은?', context_id='ctx', top_k=1, pdf_pages=(6, 7))
    assert len(calls) == 2
    lookup.assert_called_once()
    if case in ('valid', 'split'):
        assert response.result.status.value == 'ok'
        assert len(response.result.claims) == (2 if case == 'split' else 1)
        assert [c.pdf_page for c in response.result.citations] == [6, 7]
        assert len(saved) == 1
    else:
        assert response.result.reason_code.value == 'extraction_limited'
        assert not response.result.claims
        assert not saved


@pytest.mark.parametrize('case', ['all', 'missing_target', 'risky_child'])
def test_repair_mapping_preserves_order_and_detects_missing_target(repair_parts, case):
    from types import SimpleNamespace
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim, ClaimSupport
    service, saved, _ = repair_parts

    def complete(evidence, text):
        return GeneratedClaim(text, tuple(e.evidence_id for e in evidence),
            tuple(ClaimSupport(e.evidence_id, e.original_text) for e in evidence))

    def broken(evidence, text):
        first = evidence[0]
        return GeneratedClaim(text, (first.evidence_id,), (ClaimSupport(first.evidence_id, first.original_text),))

    def generate(question, evidence):
        return GeneratedAnswerDraft((broken(evidence, '대상 하나'),
            complete(evidence, '보존할 정상 주장'), broken(evidence, '대상 둘')))

    def repair(question, evidence, draft, targets):
        assert [c.text for c in draft.claims] == ['대상 하나', '대상 둘']
        if case == 'missing_target':
            # 결과 수가 두 개여도 원래 두 번째 대상이 없으면 부분 성공이다.
            return GeneratedAnswerDraft((complete(evidence, '하나 가'), complete(evidence, '하나 나')), (1, 1))
        child = broken(evidence, '위험 분할') if case == 'risky_child' else complete(evidence, '하나 나')
        # 제공자가 순서를 바꾸더라도 원래 대상 위치에 삽입한다.
        return GeneratedAnswerDraft((complete(evidence, '둘'), complete(evidence, '하나 가'), child), (2, 1, 1))

    service.generator = SimpleNamespace(generate_claims=generate, repair_claims=repair)
    response = service.answer('조건은?', context_id='ctx', top_k=1, pdf_pages=(6, 7)).result
    expected = (['하나 가', '하나 나', '보존할 정상 주장', '둘'] if case == 'all' else
                ['하나 가', '하나 나', '보존할 정상 주장'] if case == 'missing_target' else
                ['하나 가', '보존할 정상 주장', '둘'])
    assert [c.text for c in response.claims] == expected
    assert response.status.value == ('ok' if case == 'all' else 'partial')
    assert len(saved) == 1


@pytest.mark.parametrize('source', [True, 0, -1, 2, '1', None])
def test_parser_rejects_invalid_repair_source(source):
    from solo_leveling.application.evidence_qa.response_parser import parse_generated_answer, GenerationFormatError
    with pytest.raises(GenerationFormatError):
        parse_generated_answer(json.dumps({'claims': [{'text': '주장', 'evidence_ids': ['e'],
            'supports': [], 'source_claim_number': source}]}), repair_numbers={1})
