"""원문 경계와 숫자 출처의 위험 신호만 검사한다. 의미적 참·거짓을 증명하지 않는다."""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import re
from typing import Sequence

from solo_leveling.domain.evidence_qa import EvidenceInput, GeneratedClaim
from .fragments import evidence_body, fragment_flags, incomplete_tail_start, sentence_ends


_NUMBER = r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
_NUMBERS = re.compile(r'(?<![\dA-Za-z_.])' + _NUMBER + r'(?![\dA-Za-z_]|\.\d)')


def _numbers(text: str) -> set[Decimal]:
    return {Decimal(m.group().replace(',', '')) for m in _NUMBERS.finditer(text)}


def _standalone_number_risk(claim: GeneratedClaim, evidence: Sequence[EvidenceInput]) -> bool:
    isolated, inline = set(), set()
    for item in evidence:
        for line in item.original_text.splitlines():
            destination = isolated if re.fullmatch(_NUMBER, line.strip()) else inline
            destination.update(_numbers(line))
    # 번역이 페이지 번호를 본문 숫자로 바꿨어도 원문의 출처를 기준으로 판정한다.
    return bool(_numbers(claim.text) & (isolated - inline))


@dataclass(frozen=True)
class _Selection:
    evidence: EvidenceInput
    body: str
    start: int
    end: int


class ExtractionReason(str, Enum):
    NONE = 'NO_EXTRACTION_RISK'
    STANDALONE_NUMBER = 'STANDALONE_NUMBER'
    EMPTY_BODY = 'EMPTY_BODY'
    SUPPORT_NOT_FOUND = 'SUPPORT_NOT_FOUND'
    SUPPORT_LOCATION_UNRESOLVED = 'SUPPORT_LOCATION_UNRESOLVED'
    UNFINISHED_TAIL = 'UNFINISHED_TAIL'
    UNRESOLVED_PREFIX = 'UNRESOLVED_PREFIX'


@dataclass(frozen=True)
class ExtractionAssessment:
    reason: ExtractionReason
    evidence_id: str | None = None

    @property
    def risky(self) -> bool:
        return self.reason != ExtractionReason.NONE


def assess_extraction_risk(claim: GeneratedClaim, evidence: Sequence[EvidenceInput]) -> ExtractionAssessment:
    """실제 인용한 근거만 평가하며, 불명확한 구절 위치는 안전하다고 간주하지 않는다."""
    by_id = {item.evidence_id: item for item in evidence}
    cited = [by_id[eid] for eid in claim.evidence_ids]
    if _standalone_number_risk(claim, cited):
        return ExtractionAssessment(ExtractionReason.STANDALONE_NUMBER)
    selections = []
    for item in cited:
        body = evidence_body(item.original_text)
        if not body:
            return ExtractionAssessment(ExtractionReason.EMPTY_BODY, item.evidence_id)
        quotes = [s.quote_original for s in claim.supports if s.evidence_id == item.evidence_id]
        if not quotes:
            # 구절 정보 없는 기존 제공자는 청크 전체를 사용한 것으로 보수적으로 검사한다.
            selections.append(_Selection(item, body, 0, len(body)))
            continue
        for quote in quotes:
            # 숫자 줄은 위치 판정에서만 제외하며, 제거 전에도 원문에 있는 구절인지 확인한다.
            normalized = ' '.join(quote.split())
            original = ' '.join(item.original_text.split())
            if normalized not in original:
                return ExtractionAssessment(ExtractionReason.SUPPORT_NOT_FOUND, item.evidence_id)
            selected = evidence_body(quote)
            start = body.find(selected)
            if not selected or start < 0 or body.find(selected, start + 1) >= 0:
                return ExtractionAssessment(ExtractionReason.SUPPORT_LOCATION_UNRESOLVED, item.evidence_id)
            selections.append(_Selection(item, body, start, start + len(selected)))

    def joined(left: _Selection, right: _Selection) -> bool:
        # 서버가 부여한 인접 관계와 두 조각의 실제 사용 범위가 모두 맞아야 한다.
        tail = incomplete_tail_start(left.body)
        ends = sentence_ends(right.body)
        return (right.evidence.follows_evidence_id == left.evidence.evidence_id
                and tail is not None and left.start <= tail and left.end == len(left.body)
                and fragment_flags(right.body)[0] and bool(ends)
                and right.start == 0 and right.end >= ends[0])

    for selected in selections:
        tail = incomplete_tail_start(selected.body)
        if tail is not None and selected.end > tail:
            if not any(joined(selected, other) for other in selections):
                return ExtractionAssessment(ExtractionReason.UNFINISHED_TAIL, selected.evidence.evidence_id)
        ends = sentence_ends(selected.body)
        prefix_end = ends[0] if ends else len(selected.body)
        if fragment_flags(selected.body)[0] and selected.start < prefix_end:
            if not any(joined(other, selected) for other in selections):
                return ExtractionAssessment(ExtractionReason.UNRESOLVED_PREFIX, selected.evidence.evidence_id)
    return ExtractionAssessment(ExtractionReason.NONE)


def claim_has_extraction_risk(claim: GeneratedClaim, evidence: Sequence[EvidenceInput]) -> bool:
    """기존 호출부의 불리언 계약을 유지한다. 상세 판정은 첫 위험 신호만 반환한다."""
    return assess_extraction_risk(claim, evidence).risky
