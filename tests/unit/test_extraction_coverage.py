"""여러 구절로 나눈 연속 원문 인용 범위의 경계 판정을 검증한다."""

import pytest

from solo_leveling.application.evidence_qa.evidence_quality import (
    ExtractionReason, assess_extraction_risk,
)
from solo_leveling.domain.evidence_qa import EvidenceInput, GeneratedClaim, ClaimSupport


@pytest.mark.parametrize('gap,linked', [('', True), ('omitted ', True), ('', False)])
def test_split_supports_require_contiguous_coverage_and_adjacency(gap, linked):
    left = EvidenceInput('e1', 'c1', '번역', 'Faster when the sequence\n6', None, 6,
                         False, True)
    right = EvidenceInput('e2', 'c2', '번역', f'length n {gap}is smaller than d.', None, 7,
                          True, False, 'e1' if linked else None)
    claim = GeneratedClaim('n이 d보다 작을 때 빠르다.', ('e1', 'e2'), (
        ClaimSupport('e1', 'Faster when'),
        ClaimSupport('e1', 'the sequence'),
        ClaimSupport('e2', 'length n'),
        ClaimSupport('e2', 'is smaller than d.'),
    ))
    result = assess_extraction_risk(claim, (left, right))
    assert result.reason == (ExtractionReason.NONE if not gap and linked
                             else ExtractionReason.UNFINISHED_TAIL)


def test_overlapping_supports_do_not_change_original_or_ids():
    original = 'Faster when the sequence\n6'
    left = EvidenceInput('e1', 'c1', '번역', original, None, 6, False, True)
    right = EvidenceInput('e2', 'c2', '번역', 'length n is smaller than d.', None, 7,
                          True, False, 'e1')
    claim = GeneratedClaim('조건부 주장', ('e1', 'e2'), (
        ClaimSupport('e1', 'when the sequence'),
        ClaimSupport('e1', 'Faster when the'),
        ClaimSupport('e2', right.original_text),
    ))
    assert assess_extraction_risk(claim, (left, right)).reason == ExtractionReason.NONE
    assert left.original_text == original
    assert claim.evidence_ids == ('e1', 'e2')


@pytest.mark.parametrize('case,expected', [
    ('absent', 'NEXT_CANDIDATE_ABSENT'),
    ('uncited', 'NEXT_NOT_CITED'),
    ('left_start', 'LEFT_TAIL_START_MISSING'),
    ('left_end', 'LEFT_TAIL_END_MISSING'),
    ('not_continuation', 'NEXT_NOT_CONTINUATION'),
    ('no_end', 'NEXT_SENTENCE_END_ABSENT'),
    ('right_start', 'RIGHT_PREFIX_START_MISSING'),
    ('right_end', 'RIGHT_PREFIX_END_MISSING'),
    ('complete', None),
])
def test_tail_failure_details_do_not_change_risk_policy(case, expected):
    left_text = 'Faster when the sequence'
    right_text = {'not_continuation': 'A separate sentence.',
                  'no_end': 'length n is smaller than d'}.get(case, 'length n is smaller than d.')
    left = EvidenceInput('e1', 'c1', '번역', left_text, None, 6)
    right = EvidenceInput('e2', 'c2', '번역', right_text, None, 7, follows_evidence_id='e1')
    left_quote = {'left_start': 'when the sequence', 'left_end': 'Faster when'}.get(case, left_text)
    right_quote = {'right_start': 'n is smaller than d.', 'right_end': 'length n'}.get(case, right_text)
    use_next = case not in ('absent', 'uncited')
    claim = GeneratedClaim('조건부 주장', ('e1', 'e2') if use_next else ('e1',),
        (ClaimSupport('e1', left_quote),) + ((ClaimSupport('e2', right_quote),) if use_next else ()))
    result = assess_extraction_risk(claim, (left,) if case == 'absent' else (left, right))
    assert result.detail_code == expected
    assert result.reason == (ExtractionReason.NONE if expected is None else ExtractionReason.UNFINISHED_TAIL)
