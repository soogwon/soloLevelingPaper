"""외부 호출 없이 생성 응답의 엄격한 형식 변환을 검증한다."""

import json

import pytest

from solo_leveling.application.evidence_qa.response_parser import (
    GenerationFormatError, parse_generated_answer,
)


def test_valid_response_preserves_text_and_converts_arrays():
    result = parse_generated_answer(json.dumps({'claims': [
        {'text': ' 병렬 계산이 가능하다. ', 'evidence_ids': ['ev-1', 'ev-2']},
    ]}))
    assert isinstance(result.claims, tuple)
    assert result.claims[0].text == ' 병렬 계산이 가능하다. '
    assert result.claims[0].evidence_ids == ('ev-1', 'ev-2')


def test_empty_claims_is_valid_abstention():
    assert parse_generated_answer('{"claims": []}').claims == ()


@pytest.mark.parametrize('raw', [
    None, b'{}', '', '{', 'null', '[]', '{}',
    '{"claims": null}', '{"claims": {}}',
    '{"claims": [], "status": "ok"}',
    '{"claims": [], "claims": []}',
    '{"claims": NaN}', '{"claims": Infinity}',
    '```json\n{"claims": []}\n```', '{"claims": []} trailing',
    '{"claims": [null]}', '{"claims": [{"text": "주장"}]}',
    '{"claims": [{"text": "주장", "evidence_ids": "ev-1"}]}',
    '{"claims": [{"text": " ", "evidence_ids": []}]}',
    '{"claims": [{"text": true, "evidence_ids": []}]}',
    '{"claims": [{"text": "주장", "evidence_ids": [1]}]}',
    '{"claims": [{"text": "주장", "evidence_ids": [""]}]}',
    '{"claims": [{"text": "주장", "evidence_ids": [], "page": 1}]}',
    '{"claims": [{"text": "a", "text": "b", "evidence_ids": []}]}',
])
def test_invalid_format_is_rejected_without_echoing_response(raw):
    with pytest.raises(GenerationFormatError) as caught:
        parse_generated_answer(raw)
    assert str(caught.value) == '생성 응답이 정해진 JSON 형식과 일치하지 않습니다.'


def test_reference_validation_remains_service_responsibility():
    for ids in ([], ['unknown'], ['ev-1', 'ev-1']):
        result = parse_generated_answer(json.dumps({'claims': [
            {'text': '주장', 'evidence_ids': ids},
        ]}))
        assert result.claims[0].evidence_ids == tuple(ids)
