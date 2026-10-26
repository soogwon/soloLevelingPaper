"""제공자와 무관한 생성 JSON 본문을 기존 응답 계약으로 변환한다."""

import json
from enum import Enum

from solo_leveling.domain.evidence_qa import ClaimSupport, GeneratedAnswerDraft, GeneratedClaim


class GenerationFailure(str, Enum):
    FORMAT_INVALID = 'GENERATION_FORMAT_INVALID'
    JSON_INVALID = 'GENERATION_JSON_INVALID'
    SCHEMA_INVALID = 'GENERATION_SCHEMA_INVALID'


class GenerationFormatError(ValueError):
    """생성 응답의 JSON 형식이나 필드가 계약과 맞지 않는 경우."""
    def __init__(self, message: str, *, code: GenerationFailure = GenerationFailure.FORMAT_INVALID):
        self.code = code
        super().__init__(message)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('중복 JSON 키')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('JSON 비표준 상수')


def parse_generated_answer(raw_text: str, *, repair_numbers=None, support_catalog=None) -> GeneratedAnswerDraft:
    """순수 JSON만 허용하며 근거의 존재 여부와 의미는 여기서 판단하지 않는다."""
    failure = GenerationFailure.JSON_INVALID
    try:
        if not isinstance(raw_text, str):
            raise ValueError('문자열이 아님')
        payload = json.loads(raw_text, object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
        failure = GenerationFailure.SCHEMA_INVALID
        if not isinstance(payload, dict) or set(payload) != {'claims'}:
            raise ValueError('최상위 필드 불일치')
        if not isinstance(payload['claims'], list):
            raise ValueError('주장 배열이 아님')
        claims = []
        sources = []
        for item in payload['claims']:
            if repair_numbers is not None:
                if not isinstance(item, dict) or 'source_claim_number' not in item:
                    raise ValueError('보완 대상 번호 누락')
                item = dict(item)
                number = item.pop('source_claim_number')
                if type(number) is not int or number not in repair_numbers:
                    raise ValueError('보완 대상 번호 불일치')
                sources.append(number)
            if support_catalog is not None:
                if repair_numbers is None or not isinstance(item, dict) or set(item) != {'text', 'support_ids'}:
                    raise ValueError('보완 선택 필드 불일치')
                selected = item['support_ids']
                catalog = {entry['selection_id']: entry for entry in support_catalog}
                if (not isinstance(selected, list) or not selected
                        or any(not isinstance(qid, str) or qid not in catalog for qid in selected)
                        or len(selected) != len(set(selected))):
                    raise ValueError('보완 구절 ID 불일치')
                if any(number not in catalog[qid].get('source_claim_numbers', [number]) for qid in selected):
                    raise ValueError('다른 보완 대상의 구절 선택')
                quotes = {}
                for qid in selected:
                    for quote in catalog[qid]['quotes']:
                        quotes[quote['quote_id']] = quote
                item = {'text': item['text'],
                    'evidence_ids': list(dict.fromkeys(q['evidence_id'] for q in quotes.values())),
                    'supports': [{'evidence_id': q['evidence_id'], 'quote_original': q['text']} for q in quotes.values()]}
            if not isinstance(item, dict) or set(item) not in (
                {'text', 'evidence_ids'}, {'text', 'evidence_ids', 'supports'},
            ):
                raise ValueError('주장 필드 불일치')
            if not isinstance(item['evidence_ids'], list):
                raise ValueError('근거 ID 배열이 아님')
            # 기존 제공자·샘플도 읽되, 구절 정보가 없으면 품질 검사에서 보수적으로 처리한다.
            supports = item.get('supports', [])
            if not isinstance(supports, list):
                raise ValueError('근거 구절 배열이 아님')
            parsed = []
            for support in supports:
                if not isinstance(support, dict) or set(support) != {'evidence_id', 'quote_original'}:
                    raise ValueError('근거 구절 필드 불일치')
                parsed.append(ClaimSupport(support['evidence_id'], support['quote_original']))
            claims.append(GeneratedClaim(item['text'], tuple(item['evidence_ids']), tuple(parsed)))
        return GeneratedAnswerDraft(tuple(claims), tuple(sources) if repair_numbers is not None else None)
    except (ValueError, TypeError, RecursionError):
        # 원본 응답이나 제공자의 내부 정보는 오류 메시지에 포함하지 않는다.
        raise GenerationFormatError('생성 응답이 정해진 JSON 형식과 일치하지 않습니다.', code=failure) from None
