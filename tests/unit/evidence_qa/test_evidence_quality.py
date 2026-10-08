"""주장별 미완결 구절·숫자 출처 휴리스틱의 경계와 한계를 확인한다."""

from dataclasses import replace

import pytest

from solo_leveling.application.evidence_qa.evidence_quality import claim_has_extraction_risk
from solo_leveling.domain.evidence_qa import ClaimSupport, EvidenceInput, GeneratedClaim


def evidence(text, eid='e1', follows=None):
    return EvidenceInput(eid, eid, '번역', text, None, 6, follows_evidence_id=follows)


@pytest.mark.parametrize('original,quote,risky', [
    ('Parallel execution is possible. Faster when the sequence', 'Parallel execution is possible.', False),
    ('Parallel execution is possible. Faster when the sequence', 'Faster', True),
    ('Parallel execution is possible. Faster when the sequence', 'Faster when the sequence', True),
    ('Parallel execution is possible. Faster when the sequence', 'Invented complete sentence.', True),
    ('Parallel execution is possible. Faster when the sequence', 'Parallel execution\nis possible.', False),
    ('fragment at the beginning. Parallel execution is possible.', 'Parallel execution is possible.', False),
    ('fragment at the beginning. Parallel execution is possible.', 'fragment at the beginning.', True),
    ('The cost is 3.5 units. Further conditions', 'The cost is 3.5 units.', False),
    ('Same. Same. More text', 'Same.', True),
    ('An unfinished statement...', 'An unfinished statement...', True),
    ('A complete sentence.\n6', 'A complete sentence.', False),
    ('A complete sentence.\n6', '6', True),
])
def test_selected_region_not_entire_chunk_determines_risk(original, quote, risky):
    claim = GeneratedClaim('주장', ('e1',), (ClaimSupport('e1', quote),))
    assert claim_has_extraction_risk(claim, (evidence(original),)) is risky


@pytest.mark.parametrize('number,original,risky', [
    ('6', 'The method is faster.\n6', True),
    ('6.0', 'The method is faster.\n6', True),
    ('16', 'The method is faster.\n6', False),
    ('6', 'The method uses 16 units.\n6', True),
    ('6', 'The method uses 6 units.\n6', False),
    ('3.5', 'The method is faster.\n3.5', True),
    ('1000', 'The method is faster.\n1,000', True),
])
def test_number_tokens_are_not_substrings(number, original, risky):
    quote = original.splitlines()[0]
    claim = GeneratedClaim(f'수치는 {number}이다.', ('e1',), (ClaimSupport('e1', quote),))
    assert claim_has_extraction_risk(claim, (evidence(original),)) is risky


def test_uncited_inline_number_cannot_rescue_cited_page_number():
    claim = GeneratedClaim('길이는 6이다.', ('e1',))
    assert claim_has_extraction_risk(claim, (
        evidence('A complete sentence.\n6'), evidence('The length is 6.', 'e2'),
    ))


def test_cited_inline_number_removes_only_standalone_number_risk():
    claim = GeneratedClaim('길이는 6이다.', ('e1', 'e2'))
    assert not claim_has_extraction_risk(claim, (
        evidence('A complete sentence.\n6'), evidence('The length is 6.', 'e2'),
    ))


def test_join_requires_adjacent_cited_parts_covering_the_boundary():
    first = evidence('Parallel is possible. Faster when the sequence\n6')
    second = evidence('length is less than d. Unrelated unfinished', 'e2', 'e1')
    claim = GeneratedClaim('조건부 비교', ('e1', 'e2'), (
        ClaimSupport('e1', 'Faster when the sequence'),
        ClaimSupport('e2', 'length is less than d.'),
    ))
    assert not claim_has_extraction_risk(claim, (first, second))
    assert claim_has_extraction_risk(claim, (first, replace(second, follows_evidence_id=None)))
    assert claim_has_extraction_risk(replace(claim, evidence_ids=('e1',), supports=claim.supports[:1]),
                                     (first, second))
    assert claim_has_extraction_risk(replace(claim, supports=(
        ClaimSupport('e1', 'Faster'), claim.supports[1],
    )), (first, second))
    assert claim_has_extraction_risk(replace(claim, supports=(
        claim.supports[0], ClaimSupport('e2', 'less than d.'),
    )), (first, second))


def test_legacy_claim_without_spans_is_conservative():
    claim = GeneratedClaim('주장', ('e1',))
    assert claim_has_extraction_risk(claim, (evidence('Complete. Incomplete'),))
    assert not claim_has_extraction_risk(claim, (evidence('Complete.'),))


@pytest.mark.parametrize('original,quote,text,reason', [
    ('Complete.\n6', 'Complete.', '길이는 6', 'STANDALONE_NUMBER'),
    ('6', '6', '주장', 'EMPTY_BODY'),
    ('Complete.', 'Missing.', '주장', 'SUPPORT_NOT_FOUND'),
    ('Same. Same.', 'Same.', '주장', 'SUPPORT_LOCATION_UNRESOLVED'),
    ('Complete. Incomplete', 'Incomplete', '주장', 'UNFINISHED_TAIL'),
    ('fragment ends.', 'fragment ends.', '주장', 'UNRESOLVED_PREFIX'),
    ('Complete.', 'Complete.', '주장', 'NO_EXTRACTION_RISK'),
])
def test_assessment_preserves_boolean_and_reports_first_reason(original, quote, text, reason):
    from solo_leveling.application.evidence_qa.evidence_quality import assess_extraction_risk
    claim = GeneratedClaim(text, ('e1',), (ClaimSupport('e1', quote),))
    inputs = (evidence(original),)
    result = assess_extraction_risk(claim, inputs)
    assert result.reason.value == reason
    assert result.risky == claim_has_extraction_risk(claim, inputs)
    assert result.evidence_id == (None if reason in ('STANDALONE_NUMBER', 'NO_EXTRACTION_RISK') else 'e1')
