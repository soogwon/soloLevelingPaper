"""제공자와 무관한 생성 JSON 본문을 기존 응답 계약으로 변환한다."""

import json

from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim


class GenerationFormatError(ValueError):
    """생성 응답의 JSON 형식이나 필드가 계약과 맞지 않는 경우."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('중복 JSON 키')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('JSON 비표준 상수')


def parse_generated_answer(raw_text: str) -> GeneratedAnswerDraft:
    """순수 JSON만 허용하며 근거의 존재 여부와 의미는 여기서 판단하지 않는다."""
    try:
        if not isinstance(raw_text, str):
            raise ValueError('문자열이 아님')
        payload = json.loads(raw_text, object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
        if not isinstance(payload, dict) or set(payload) != {'claims'}:
            raise ValueError('최상위 필드 불일치')
        if not isinstance(payload['claims'], list):
            raise ValueError('주장 배열이 아님')
        claims = []
        for item in payload['claims']:
            if not isinstance(item, dict) or set(item) != {'text', 'evidence_ids'}:
                raise ValueError('주장 필드 불일치')
            if not isinstance(item['evidence_ids'], list):
                raise ValueError('근거 ID 배열이 아님')
            claims.append(GeneratedClaim(item['text'], tuple(item['evidence_ids'])))
        return GeneratedAnswerDraft(tuple(claims))
    except (ValueError, TypeError, RecursionError):
        # 원본 응답이나 제공자의 내부 정보는 오류 메시지에 포함하지 않는다.
        raise GenerationFormatError('생성 응답이 정해진 JSON 형식과 일치하지 않습니다.') from None
