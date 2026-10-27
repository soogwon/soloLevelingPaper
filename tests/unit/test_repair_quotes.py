"""보완 선택지의 범위·원문·앞뒤 연결을 API 호출 없이 검증한다."""

import json
from dataclasses import replace
import pytest

from solo_leveling.domain.evidence_qa import EvidenceInput, GeneratedClaim, GeneratedAnswerDraft
from solo_leveling.application.evidence_qa.repair_quotes import repair_quotes
from solo_leveling.application.evidence_qa.response_parser import parse_generated_answer, GenerationFormatError
from solo_leveling.application.evidence_qa.evidence_quality import assess_extraction_risk


@pytest.fixture
def pair():
    return (
        EvidenceInput('left', 'c1', '번역 앞',
            'fragment from before. A complete fact. Faster when the sequence\n6', None, 6, True, True),
        EvidenceInput('right', 'c2', '번역 뒤', 'length n is smaller than d. Another fact.',
                      None, 7, True, False, 'left'),
    )


def target():
    return [{'claim_number': 1, 'evidence_id': 'left', 'next_evidence_id': 'right',
             'reason_code': 'UNFINISHED_TAIL'}]


def parse(options, selected, text='n이 d보다 작을 때 더 빠르다.'):
    return parse_generated_answer(json.dumps({'claims': [{'text': text,
        'source_claim_number': 1, 'support_ids': selected}]}), repair_numbers={1}, support_catalog=options)


def test_pair_is_atomic_and_restores_exact_separate_citations(pair):
    options = repair_quotes(pair, target())
    assert options == repair_quotes(pair, target())
    selected = next(o for o in options if len(o['quotes']) == 2)
    assert [q['pdf_page'] for q in selected['quotes']] == [6, 7]
    draft = parse(options, [selected['selection_id']])
    claim, = draft.claims
    assert claim.evidence_ids == ('left', 'right') and draft.repair_sources == (1,)
    assert claim.supports[0].quote_original == 'Faster when the sequence\n6'
    assert claim.supports[1].quote_original == 'length n is smaller than d.'
    assert not assess_extraction_risk(claim, pair).risky
    for option in options:
        for q in option['quotes']:
            original = next(e for e in pair if e.evidence_id == q['evidence_id'])
            assert q['text'] == original.original_text[q['start']:q['end']]
        if len(option['quotes']) == 1:
            assert 'Faster when' not in option['quotes'][0]['text']


def test_scoped_out_next_is_not_added_and_complete_alternative_survives(pair):
    # 상위 계층에서 페이지·섹션 범위를 적용한 후보만 전달한다.
    options = repair_quotes(pair[:1], target())
    assert all(len(o['quotes']) == 1 for o in options)
    assert all(q['evidence_id'] == 'left' for o in options for q in o['quotes'])
    selected = next(o for o in options if o['quotes'][0]['text'] == 'A complete fact.')
    claim, = parse(options, [selected['selection_id']], '완결된 다른 사실').claims
    assert not assess_extraction_risk(claim, pair[:1]).risky


@pytest.mark.parametrize('kind', ['wrong_link', 'unfinished_right', 'empty_right'])
def test_invalid_continuation_does_not_become_pair(pair, kind):
    right = replace(pair[1], follows_evidence_id=None) if kind == 'wrong_link' else replace(
        pair[1], original_text='length n is smaller' if kind == 'unfinished_right' else '7')
    assert all(len(o['quotes']) == 1 for o in repair_quotes((pair[0], right), target()))


@pytest.mark.parametrize('kind', ['unknown', 'stale', 'duplicate', 'empty', 'raw_quote_id', 'legacy'])
def test_selection_reference_errors_fail_closed(pair, kind):
    options = repair_quotes(pair, target())
    option = next(o for o in options if len(o['quotes']) == 2)
    ids = [option['selection_id']]
    if kind == 'unknown':
        ids = ['private-invalid']
    elif kind == 'stale':
        changed = (replace(pair[0], original_text=pair[0].original_text.replace('Faster', 'Better')), pair[1])
        ids = [next(o for o in repair_quotes(changed, target()) if len(o['quotes']) == 2)['selection_id']]
    elif kind == 'duplicate':
        ids *= 2
    elif kind == 'empty':
        ids = []
    elif kind == 'raw_quote_id':
        ids = [option['quotes'][0]['quote_id']]
    with pytest.raises(GenerationFormatError) as error:
        if kind == 'legacy':
            parse_generated_answer(json.dumps({'claims': [{'text': '주장', 'source_claim_number': 1,
                'evidence_ids': ['left'], 'supports': []}]}), repair_numbers={1}, support_catalog=options)
        else:
            parse(options, ids)
    assert 'private' not in str(error.value)


def test_no_usable_options_still_allows_abstention(pair):
    broken = (replace(pair[0], original_text='Faster when the sequence\n6'),)
    options = repair_quotes(broken, target())
    assert options == []
    result = parse_generated_answer('{"claims": []}', repair_numbers={1}, support_catalog=options)
    assert not result.claims and result.repair_sources == ()


def test_target_scope_removes_other_question_evidence_but_preserves_pair(pair):
    other = EvidenceInput('path', 'c3', '경로 설명', 'Short paths help learning.', None, 6)
    draft = GeneratedAnswerDraft((GeneratedClaim('계산 복잡도 조건', ('left',)),))
    options = repair_quotes((*pair, other), target(), draft)
    assert all(q['evidence_id'] != 'path' for o in options for q in o['quotes'])
    selected = next(o for o in options if len(o['quotes']) == 2)
    claim, = parse(options, [selected['selection_id']]).claims
    assert claim.evidence_ids == ('left', 'right')
    assert not assess_extraction_risk(claim, pair).risky
    unrelated = repair_quotes((other,), [], GeneratedAnswerDraft((GeneratedClaim('경로', ('path',)),)))
    with pytest.raises(GenerationFormatError):
        parse(options, [unrelated[0]['selection_id']], '경로가 짧을수록 쉽다.')


def test_cannot_borrow_quote_from_different_repair_target(pair):
    other = EvidenceInput('path', 'c3', '경로 설명', 'Short paths help learning.', None, 6)
    draft = GeneratedAnswerDraft((GeneratedClaim('조건', ('left',)), GeneratedClaim('경로', ('path',))))
    options = repair_quotes((*pair, other), target(), draft)
    option = next(o for o in options if o['source_claim_numbers'] == [2])
    with pytest.raises(GenerationFormatError):
        parse(options, [option['selection_id']])


def test_continuation_options_exclude_other_sentences_in_same_chunks(pair):
    draft = GeneratedAnswerDraft((GeneratedClaim('계산 복잡도 조건', ('left',)),))
    options = repair_quotes(pair, target(), draft)
    option, = options
    assert [q['text'] for q in option['quotes']] == [
        'Faster when the sequence\n6', 'length n is smaller than d.']
    # 과거 단독 선택지 ID를 가져와도 현재 보완에서는 선택할 수 없다.
    extra = next(o for o in repair_quotes(pair, []) if o['quotes'][0]['text'] == 'Another fact.')
    with pytest.raises(GenerationFormatError):
        parse(options, [extra['selection_id']], '추가 설명')
    claim, = parse(options, [option['selection_id']]).claims
    assert not assess_extraction_risk(claim, pair).risky


def test_continuation_without_in_scope_completion_abstains(pair):
    draft = GeneratedAnswerDraft((GeneratedClaim('조건', ('left',)),))
    assert repair_quotes(pair[:1], target(), draft) == []


def test_continuation_preserves_line_breaks_and_decimal_condition(pair):
    right = replace(pair[1], original_text='length n is smaller than\n0.5 times d. Extra explanation.')
    draft = GeneratedAnswerDraft((GeneratedClaim('조건', ('left',)),))
    option, = repair_quotes((pair[0], right), target(), draft)
    assert option['quotes'][1]['text'] == 'length n is smaller than\n0.5 times d.'
