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
    AMBIGUOUS_OVERLAP = 'AMBIGUOUS_OVERLAP'


@dataclass(frozen=True)
class ExtractionAssessment:
    reason: ExtractionReason
    evidence_id: str | None = None
    detail_code: str | None = None

    @property
    def risky(self) -> bool:
        return self.reason != ExtractionReason.NONE


def _select_claim(claim: GeneratedClaim, evidence: Sequence[EvidenceInput]):
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
            # 겹쳐 반복되는 구절도 모두 찾아 위치가 유일한 경우에만 사용한다.
            pattern = r'\s+'.join(re.escape(word) for word in quote.split())
            matches = list(re.finditer(f'(?=({pattern}))', item.original_text))
            positions = (_body_range(item.original_text, matches[0].start(1), matches[0].end(1))
                         if len(matches) == 1 else None)
            if positions is None:
                return ExtractionAssessment(ExtractionReason.SUPPORT_LOCATION_UNRESOLVED, item.evidence_id)
            selections.append(_Selection(item, body, *positions))

    # 같은 근거 안에서 겹치거나 공백만 사이에 둔 구절은 실제 인용 범위로 묶는다.
    # 선택하지 않은 단어·조건절을 건너뛰거나 서로 다른 근거의 본문을 합치지는 않는다.
    covered = []
    for item in cited:
        for selected in sorted((s for s in selections if s.evidence == item),
                               key=lambda s: (s.start, s.end)):
            if (covered and covered[-1].evidence == item
                    and (selected.start <= covered[-1].end
                         or not selected.body[covered[-1].end:selected.start].strip())):
                previous = covered[-1]
                covered[-1] = _Selection(item, selected.body, previous.start,
                                         max(previous.end, selected.end))
            else:
                covered.append(selected)
    return covered


def _conflicting_joined_comparisons(claim: GeneratedClaim, left: _Selection, right: _Selection,
                                    selections: Sequence[_Selection]) -> bool:
    """겹친 두 번역의 서로 다른 비교 조건을 한 원문 조건에서 가져온 경우를 감지한다."""
    def conditions(text):
        return set(re.findall(r'[가-힣]+수록', text or ''))

    asserted = conditions(claim.text)
    first = conditions(left.evidence.text_ko) & asserted
    second = conditions(right.evidence.text_ko) & asserted
    if not (first - second and second - first):
        return False
    # 하나의 영어 상관 비교에 근거한 경우만 판정한다. 추가 원문을 인용했거나
    # 하나 이상의 조건이 명시된 경우에는 구조만으로 추가 비교를 배제하지 않는다.
    if len(selections) != 2:
        return False
    tail = incomplete_tail_start(left.body)
    end = sentence_ends(right.body)[0]
    if left.start != tail or right.end != end:
        return False
    start, continuation = left.body[tail:], right.body[:end]
    # 단어 중간에서 시작하는 중복 구간도 한 번만 센다.
    overlap = next((len(start) - i for i in range(len(start))
                    if start[i] == continuation[0] and continuation.startswith(start[i:])), 0)
    source = start + (' ' if not overlap else '') + continuation[overlap:]
    comparisons = re.findall(r'\bthe\s+(?:shorter|longer|smaller|larger|greater|'
                             r'easier|harder|fewer|more|less|better|worse)\b', source, re.I)
    return len(comparisons) == 2 and not re.search(r'\b(?:and|or)\s+(?:shorter|longer|'
        r'smaller|larger|greater|more|less|fewer)\b', source, re.I)


def assess_extraction_risk(claim: GeneratedClaim, evidence: Sequence[EvidenceInput]) -> ExtractionAssessment:
    """실제 인용한 근거만 평가하며, 불명확한 구절 위치는 안전하다고 간주하지 않는다."""
    selections = _select_claim(claim, evidence)
    if isinstance(selections, ExtractionAssessment):
        return selections

    def joined(left: _Selection, right: _Selection) -> bool:
        # 서버가 부여한 인접 관계와 두 조각의 실제 사용 범위가 모두 맞아야 한다.
        tail = incomplete_tail_start(left.body)
        ends = sentence_ends(right.body)
        return (right.evidence.follows_evidence_id == left.evidence.evidence_id
                and tail is not None and left.start <= tail and left.end == len(left.body)
                and fragment_flags(right.body)[0] and bool(ends)
                and right.start == 0 and right.end >= ends[0])

    def tail_detail(selected: _Selection, tail: int) -> str:
        # 여러 조건이 실패하면 후보 존재 → 인용 여부 → 구절 경계 순서로 첫 사유를 남긴다.
        following = [item for item in evidence
                     if item.follows_evidence_id == selected.evidence.evidence_id]
        if not following:
            return 'NEXT_CANDIDATE_ABSENT'
        following_ids = {item.evidence_id for item in following}
        right_parts = [s for s in selections if s.evidence.evidence_id in following_ids]
        if not right_parts:
            return 'NEXT_NOT_CITED'
        if selected.start > tail:
            return 'LEFT_TAIL_START_MISSING'
        if selected.end < len(selected.body):
            return 'LEFT_TAIL_END_MISSING'
        prefixes = [s for s in right_parts if fragment_flags(s.body)[0]]
        if not prefixes:
            return 'NEXT_NOT_CONTINUATION'
        completed = [s for s in prefixes if sentence_ends(s.body)]
        if not completed:
            return 'NEXT_SENTENCE_END_ABSENT'
        starts = [s for s in completed if s.start == 0]
        if not starts:
            return 'RIGHT_PREFIX_START_MISSING'
        return 'RIGHT_PREFIX_END_MISSING'

    cited_sentences = {}
    for selected in selections:
        tail = incomplete_tail_start(selected.body)
        if tail is not None and selected.end > tail:
            following = [other for other in selections if joined(selected, other)]
            if not following:
                return ExtractionAssessment(ExtractionReason.UNFINISHED_TAIL,
                    selected.evidence.evidence_id, tail_detail(selected, tail))
            if any(_conflicting_joined_comparisons(claim, selected, other, selections)
                   for other in following):
                return ExtractionAssessment(ExtractionReason.AMBIGUOUS_OVERLAP, selected.evidence.evidence_id)
        ends = sentence_ends(selected.body)
        prefix_end = ends[0] if ends else len(selected.body)
        if fragment_flags(selected.body)[0] and selected.start < prefix_end:
            if not any(joined(other, selected) for other in selections):
                return ExtractionAssessment(ExtractionReason.UNRESOLVED_PREFIX, selected.evidence.evidence_id)
        # 문장별 번역 정렬이 없어 어느 쪽이 맞는지 단정할 수 없다.
        # 이 주장이 같은 페이지에서 실제로 중복 인용한 완결 문장만 비교한다.
        if len(claim.evidence_ids) > 1:
            translation = ' '.join((selected.evidence.text_ko or '').split())
            for sentence in _cited_complete_sentences(selected):
                key = (selected.evidence.pdf_page, sentence)
                previous = cited_sentences.setdefault(key, (selected.evidence.chunk_id, translation))
                if previous[0] != selected.evidence.chunk_id and previous[1] != translation:
                    return ExtractionAssessment(ExtractionReason.AMBIGUOUS_OVERLAP, selected.evidence.evidence_id)
    return ExtractionAssessment(ExtractionReason.NONE)


def _body_range(raw: str, start: int, end: int):
    """숫자 줄 제거 여부를 전체 청크에서 결정하고 원문 구간을 본문 좌표로 옮긴다."""
    offset = 0
    body_offset = 0
    positions = []
    for line in raw.splitlines(keepends=True):
        if not line.strip().isdigit():
            for token in re.finditer(r'\S+', line):
                left, right = offset + token.start(), offset + token.end()
                if left < end and right > start:
                    positions.append((body_offset + max(start - left, 0),
                                      body_offset + min(end, right) - left))
                body_offset += len(token.group()) + 1
        offset += len(line)
    return (positions[0][0], positions[-1][1]) if positions else None


def _cited_complete_sentences(selected: _Selection):
    # 경계 검사 좌표는 숫자 줄을 제외하지만, 문장의 동일성 비교에는 원래 숫자가 필요하다.
    raw = selected.evidence.original_text
    start = 0
    for end in sentence_ends(raw):
        original = raw[start:end]
        positions = _body_range(raw, start, end)
        if (positions is not None
                and selected.start <= positions[0] and positions[1] <= selected.end
                and (start or not selected.evidence.starts_mid_sentence)):
            yield ' '.join(original.split())
        start = end


def assess_answer_overlaps(claims: Sequence[GeneratedClaim], evidence: Sequence[EvidenceInput]):
    """답변 전체에서 번역이 다른 중복 문장을 인용한 모든 주장의 위치를 반환한다."""
    cited_sentences = {}
    conflicts = {}
    for number, claim in enumerate(claims):
        selections = _select_claim(claim, evidence)
        if isinstance(selections, ExtractionAssessment):
            continue
        for selected in selections:
            item = selected.evidence
            translation = ' '.join((item.text_ko or '').split())
            for sentence in _cited_complete_sentences(selected):
                key = (item.pdf_page, sentence)
                previous = cited_sentences.setdefault(key, [])
                for other_number, other in previous:
                    if (other.chunk_id != item.chunk_id
                            and ' '.join((other.text_ko or '').split()) != translation):
                        conflicts[other_number] = ExtractionAssessment(
                            ExtractionReason.AMBIGUOUS_OVERLAP, other.evidence_id)
                        conflicts[number] = ExtractionAssessment(
                            ExtractionReason.AMBIGUOUS_OVERLAP, item.evidence_id)
                previous.append((number, item))
    return conflicts


def claim_has_extraction_risk(claim: GeneratedClaim, evidence: Sequence[EvidenceInput]) -> bool:
    """기존 호출부의 불리언 계약을 유지한다. 상세 판정은 첫 위험 신호만 반환한다."""
    return assess_extraction_risk(claim, evidence).risky
