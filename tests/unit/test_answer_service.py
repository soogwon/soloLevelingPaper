"""답변 생성의 구조 검증과 저장 순서를 검증한다."""

from types import SimpleNamespace

import pytest

from solo_leveling.application.evidence_qa.answer import AnswerService, AnswerGenerationError
from solo_leveling.application.evidence_qa.entry import ContextSearchResponse
from solo_leveling.application.evidence_qa.ports import GenerationUnavailable
from solo_leveling.application.evidence_qa.serialization import serialize_answer_response
from solo_leveling.domain.evidence_qa import (
    AnswerStatus, ClaimSupport, GeneratedAnswerDraft, GeneratedClaim, ReasonCode, RetrievedChunk,
    RetrievalMethod, SearchResult, SearchScope,
)
from solo_leveling.domain.models import Chunk
from solo_leveling.infrastructure.generation.fake_generator import FakeClaimGenerator


@pytest.fixture
def parts():
    result = SearchResult('질문', SearchScope('v', 'p', 't'), RetrievalMethod.VECTOR, 'idx', tuple(
        RetrievedChunk(Chunk(f'c{i}', 'p', i, f'Original {i}.', f'한국어 근거 {i}', 't', pdf_page=i+1), 1., i+1)
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


@pytest.mark.parametrize('original,text,quote,expected', [
    ('Parallel is possible. Faster when the sequence\n6', '시퀀스 길이 6에서 더 빠르다.',
     'Faster when the sequence\n6', AnswerStatus.PARTIAL),
    ('Parallel is possible. Faster when the sequence', '더 빠르다.',
     'Faster when the sequence', AnswerStatus.PARTIAL),
    ('Parallel is possible. Faster when the sequence', '병렬 처리가 가능하다.',
     'Parallel is possible.', AnswerStatus.OK),
    ('The sequence length is 6.\n6', '시퀀스 길이는 6이다.',
     'The sequence length is 6.', AnswerStatus.OK),
    ('A complete sentence.', '주장', '원문에 없는 구절', AnswerStatus.INSUFFICIENT_EVIDENCE),
])
def test_extraction_quality_status_and_serialization(parts, original, text, quote, expected):
    from dataclasses import replace
    service, search, saved, _ = parts
    item = replace(search.items[0], chunk=replace(search.items[0].chunk, original_text=original))
    service.search = SimpleNamespace(search=lambda *a, **kw:
        ContextSearchResponse('ctx', replace(search, items=(item,))))
    def generate(question, evidence):
        eid = evidence[0].evidence_id
        return GeneratedAnswerDraft((GeneratedClaim(text, (eid,), (ClaimSupport(eid, quote),)),))
    service.generator = SimpleNamespace(generate_claims=generate)
    response = service.answer('조건은?', context_id='ctx')
    assert response.result.status == expected
    if expected == AnswerStatus.INSUFFICIENT_EVIDENCE:
        assert not response.result.claims
        assert not saved
        assert response.result.reason_code == ReasonCode.EXTRACTION_LIMITED
        return
    assert response.result.claims[0].text == text
    assert saved[0][1][0].quote_original == original
    payload = serialize_answer_response(response)
    assert payload['verification_level'] == 'structural_only'
    assert 'supports' not in payload['answer']['claims'][0]
    if expected == AnswerStatus.PARTIAL:
        assert response.result.reason_code == ReasonCode.EXTRACTION_LIMITED
        assert payload['answer']['reason_code'] == 'extraction_limited'
        assert '1번 주장' in response.result.answer_ko
    else:
        assert response.result.reason_code is None


def test_one_risky_claim_makes_response_partial_without_retry(parts):
    from dataclasses import replace
    service, search, saved, _ = parts
    originals = ('Complete sentence.', 'Faster when the sequence\n6')
    items = tuple(replace(item, chunk=replace(item.chunk, original_text=text))
                  for item, text in zip(search.items, originals))
    service.search = SimpleNamespace(search=lambda *a, **kw:
        ContextSearchResponse('ctx', replace(search, items=items)))
    calls = []
    def generate(question, evidence):
        calls.append(1)
        return GeneratedAnswerDraft(tuple(GeneratedClaim(text, (e.evidence_id,),
            (ClaimSupport(e.evidence_id, e.original_text),))
            for text, e in zip(('정상 주장', '길이 6에서 더 빠르다.'), evidence)))
    service.generator = SimpleNamespace(generate_claims=generate)
    response = service.answer('조건은?', context_id='ctx', top_k=2)
    assert response.result.status == AnswerStatus.PARTIAL
    assert '2번 주장' in response.result.answer_ko
    assert len(response.result.claims) == len(saved[0][1]) == 2
    assert calls == [1]


def test_support_cannot_reference_an_uncited_evidence(parts):
    service, _, saved, _ = parts
    def generate(question, evidence):
        return GeneratedAnswerDraft((GeneratedClaim('주장', (evidence[0].evidence_id,),
            (ClaimSupport(evidence[1].evidence_id, evidence[1].original_text),)),))
    service.generator = SimpleNamespace(generate_claims=generate)
    response = service.answer('질문', context_id='ctx')
    assert response.result.reason_code == ReasonCode.VERIFICATION_FAILED
    assert not saved


@pytest.mark.parametrize('cite_next', [True, False])
def test_continuation_must_be_used_by_same_claim_to_complete_condition(parts, cite_next):
    from dataclasses import replace
    service, search, _, _ = parts
    originals = ('Faster when the sequence\n6', 'length n is smaller than d.')
    items = tuple(replace(item, chunk=replace(item.chunk, original_text=text, pdf_page=6+i))
                  for i, (item, text) in enumerate(zip(search.items, originals)))
    service.search = SimpleNamespace(search=lambda *a, **kw:
        ContextSearchResponse('ctx', replace(search, items=items)))
    def generate(question, evidence):
        used = evidence if cite_next else evidence[:1]
        return GeneratedAnswerDraft((GeneratedClaim('n이 d보다 작을 때 더 빠르다.',
            tuple(e.evidence_id for e in used),
            tuple(ClaimSupport(e.evidence_id, e.original_text) for e in used)),))
    service.generator = SimpleNamespace(generate_claims=generate)
    response = service.answer('조건은?', context_id='ctx')
    assert response.result.status == (AnswerStatus.OK if cite_next else AnswerStatus.PARTIAL)
    assert [c.pdf_page for c in response.result.citations] == ([6, 7] if cite_next else [6])


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


def test_fragment_flags_are_passed_to_generator(parts):
    from dataclasses import replace
    service, result, _, _ = parts
    chunks = ('ation of positions, the easier it is to learn.', 'Complete sentence [12].')
    items = tuple(replace(item, chunk=replace(item.chunk, original_text=text))
                  for item, text in zip(result.items, chunks))
    service.search = SimpleNamespace(
        search=lambda *a, **kw: ContextSearchResponse('ctx', replace(result, items=items)))
    received = []

    def generate(question, evidence):
        received.extend(evidence)
        return GeneratedAnswerDraft((GeneratedClaim('주장', (evidence[1].evidence_id,)),))

    service.generator = SimpleNamespace(generate_claims=generate)
    service.answer('질문', context_id='ctx')
    flags = [(item.starts_mid_sentence, item.ends_mid_sentence) for item in received]
    assert flags == [(True, False), (False, False)]


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


@pytest.mark.parametrize('cite_next', [False, True])
def test_diagnostics_distinguishes_unused_continuation(parts, capsys, cite_next):
    import json
    from dataclasses import replace
    service, search, _, _ = parts
    first = replace(search.items[0].chunk, pdf_page=6,
                    original_text='SECRET faster when the sequence', text='비공개 번역')
    second = replace(search.items[1].chunk, pdf_page=7,
                     original_text='length is less than d.', text='비공개 번역')
    search = replace(search, items=(replace(search.items[0], chunk=first),))
    service.search = SimpleNamespace(search=lambda *a, **kw: ContextSearchResponse('ctx', search))
    service.continuation_reader = SimpleNamespace(following_chunks=lambda *a: (second,))

    def generate(question, evidence):
        chosen = evidence if cite_next else evidence[:1]
        return GeneratedAnswerDraft((GeneratedClaim('비공개 주장',
            tuple(e.evidence_id for e in chosen),
            tuple(ClaimSupport(e.evidence_id, e.original_text) for e in chosen)),))

    service.generator = SimpleNamespace(generate_claims=generate)
    response = service.answer('SECRET question', context_id='ctx')
    captured = capsys.readouterr()
    assert captured.out == ''
    logs = [json.loads(line) for line in captured.err.splitlines()]
    assert all(secret not in json.dumps(logs, ensure_ascii=False)
               for secret in ('SECRET', '비공개', 'length is less than d.'))
    trace = [r for r in logs if r['stage'] == 'evidence_trace']
    assert len({r['request_id'] for r in logs}) == 1
    assert len({r['attempt_id'] for r in trace}) == 1
    candidates = [r for r in trace if r['event'] == 'candidate']
    assert [r['pdf_page'] for r in candidates] == [6, 7]
    assert [r['supplemental'] for r in candidates] == [False, True]
    assert candidates[1]['follows_evidence_id'] == candidates[0]['evidence_id']
    selected = [r for r in trace if r['event'] == 'claim_selection']
    assert [r['evidence_id'] for r in selected] == [
        r['evidence_id'] for r in (candidates if cite_next else candidates[:1])]
    assert all(r['support_count'] == 1 for r in selected)
    quality = [r for r in trace if r['event'] == 'quality']
    assert quality[0]['reason_code'] == ('NO_EXTRACTION_RISK' if cite_next else 'UNFINISHED_TAIL')
    assert response.result.status == (AnswerStatus.OK if cite_next else AnswerStatus.PARTIAL)


@pytest.fixture
def repair_parts(parts):
    from dataclasses import replace
    from unittest.mock import Mock
    service, search, saved, _ = parts
    first = replace(search.items[0].chunk, pdf_page=6,
                    original_text='Faster when the sequence\n6')
    second = replace(search.items[1].chunk, pdf_page=7,
                     original_text='length n is less than d.')
    search = replace(search, scope=replace(search.scope, pdf_pages=(6, 7)),
                     items=(replace(search.items[0], chunk=first),))
    lookup = Mock(return_value=ContextSearchResponse('ctx', search))
    service.search = SimpleNamespace(search=lookup)
    service.continuation_reader = SimpleNamespace(following_chunks=lambda *a: (second,))

    def generate(question, evidence):
        item = evidence[0]
        return GeneratedAnswerDraft((GeneratedClaim('최초 불완전 주장', (item.evidence_id,),
            (ClaimSupport(item.evidence_id, item.original_text),)),))

    def repair(question, evidence, draft, targets):
        return GeneratedAnswerDraft((GeneratedClaim('n이 d보다 작을 때 더 빠르다.',
            tuple(e.evidence_id for e in evidence),
            tuple(ClaimSupport(e.evidence_id, e.original_text) for e in evidence)),))

    service.generator = SimpleNamespace(generate_claims=Mock(side_effect=generate),
                                        repair_claims=Mock(side_effect=repair))
    return service, saved, lookup


def test_repair_once_uses_same_candidates_and_saves_only_final(repair_parts, capsys):
    service, saved, lookup = repair_parts
    response = service.answer('조건은?', context_id='ctx', top_k=1, pdf_pages=(6, 7))
    assert response.result.status == AnswerStatus.OK
    assert [c.pdf_page for c in response.result.citations] == [6, 7]
    assert response.result.claims[0].text == 'n이 d보다 작을 때 더 빠르다.'
    lookup.assert_called_once()
    service.generator.generate_claims.assert_called_once()
    service.generator.repair_claims.assert_called_once()
    original_args = service.generator.generate_claims.call_args.args
    repair_args = service.generator.repair_claims.call_args.args
    assert repair_args[:2] == original_args
    assert repair_args[2].claims[0].text == '최초 불완전 주장'
    target, = repair_args[3]
    assert target.claim_number == 1
    assert target.evidence_id == repair_args[1][0].evidence_id
    assert target.next_evidence_id == repair_args[1][1].evidence_id
    assert len(saved) == 1
    assert {e.chunk_id for e in saved[0][1]} == {'c0', 'c1'}
    import json
    captured = capsys.readouterr()
    assert captured.out == ''
    assert all(s not in json.dumps([json.loads(line) for line in captured.err.splitlines()],
                                  ensure_ascii=False) for s in ('최초 불완전 주장', 'Faster when', '조건은?'))
    logs = [json.loads(line) for line in captured.err.splitlines()]
    assert [r['round_number'] for r in logs if r['event'] == 'quality'] == [0, 1]
    assert [r['reason_code'] for r in logs if r['event'] == 'quality'] == ['UNFINISHED_TAIL', 'NO_EXTRACTION_RISK']
    assert next(r for r in logs if r['event'] == 'repair_target')['next_evidence_id'] == target.next_evidence_id
    assert next(r for r in logs if r['event'] == 'repair_outcome')['reason_code'] == 'REPAIR_ADOPTED'


@pytest.mark.parametrize('outcome', ['unavailable', 'format', 'unknown_id', 'wrong_type', 'still_partial', 'empty'])
def test_repair_failure_and_empty_never_loop(repair_parts, outcome, capsys):
    from solo_leveling.application.evidence_qa.response_parser import GenerationFormatError
    service, saved, lookup = repair_parts

    def repair(question, evidence, draft, targets):
        if outcome == 'unavailable':
            raise GenerationUnavailable('비공개 오류')
        if outcome == 'format':
            raise GenerationFormatError('비공개 형식')
        if outcome == 'unknown_id':
            return GeneratedAnswerDraft((GeneratedClaim('잘못된 수정', ('unknown',)),))
        if outcome == 'wrong_type':
            return {'claims': []}
        return GeneratedAnswerDraft(()) if outcome == 'empty' else draft

    service.generator.repair_claims.side_effect = repair
    response = service.answer('조건은?', context_id='ctx', top_k=1)
    import json
    failures = [r for line in capsys.readouterr().err.splitlines()
                if (r := json.loads(line))['event'] == 'verification_failure']
    expected = {'format': 'GENERATION_FORMAT_INVALID', 'unknown_id': 'UNKNOWN_EVIDENCE_ID',
                'wrong_type': 'DRAFT_INVALID'}.get(outcome)
    assert [r['reason_code'] for r in failures] == ([expected] if expected else [])
    assert all(r['round_number'] == 1 for r in failures)
    lookup.assert_called_once()
    service.generator.repair_claims.assert_called_once()
    assert response.result.status == AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.result.reason_code == ReasonCode.EXTRACTION_LIMITED
    assert not response.result.claims
    assert not saved


@pytest.mark.parametrize('case', ['no_next', 'already_cited', 'complete', 'number_only', 'unsupported'])
def test_no_repair_outside_eligible_tail(repair_parts, case):
    service, saved, lookup = repair_parts
    if case == 'no_next':
        service.continuation_reader = None
    elif case == 'unsupported':
        del service.generator.repair_claims
    elif case == 'complete':
        from dataclasses import replace
        found = lookup.return_value
        item = found.result.items[0]
        lookup.return_value = replace(found, result=replace(found.result,
            items=(replace(item, chunk=replace(item.chunk, original_text='Complete sentence.')),)))
        service.continuation_reader = None
    else:
        def generate(question, evidence):
            if case == 'already_cited':
                return GeneratedAnswerDraft((GeneratedClaim('주장', tuple(e.evidence_id for e in evidence),
                    tuple(ClaimSupport(e.evidence_id, e.original_text) for e in evidence)),))
            e = evidence[0]
            return GeneratedAnswerDraft((GeneratedClaim('길이는 6이다.', (e.evidence_id,),
                (ClaimSupport(e.evidence_id, e.original_text),)),))
        service.generator.generate_claims.side_effect = generate
    service.answer('조건은?', context_id='ctx', top_k=1)
    lookup.assert_called_once()
    if case != 'unsupported':
        service.generator.repair_claims.assert_not_called()


@pytest.mark.parametrize('case,code', [
    ('syntax', 'GENERATION_JSON_INVALID'),
    ('schema', 'GENERATION_SCHEMA_INVALID'),
    ('format', 'GENERATION_FORMAT_INVALID'),
    ('unknown', 'UNKNOWN_EVIDENCE_ID'),
    ('duplicate', 'DUPLICATE_EVIDENCE_ID'),
    ('empty_refs', 'EMPTY_EVIDENCE_IDS'),
    ('support', 'SUPPORT_REFERENCE_INVALID'),
    ('draft', 'DRAFT_INVALID'),
    ('normal', None),
])
def test_compound_draft_failure_classification(parts, capsys, case, code):
    import json
    from solo_leveling.application.evidence_qa.response_parser import GenerationFormatError, parse_generated_answer
    service, _, saved, _ = parts

    def generate(question, evidence):
        if case == 'syntax':
            return parse_generated_answer('{"claims": [SECRET')
        if case == 'schema':
            return parse_generated_answer('{"claims": "SECRET"}')
        if case == 'format':
            raise GenerationFormatError('SECRET provider response')
        if case == 'draft':
            return {'claims': 'SECRET'}
        first, second = (e.evidence_id for e in evidence)
        ids = {'unknown': ('SECRET-invalid-id',), 'duplicate': (second, second),
               'empty_refs': ()}.get(case, (second,))
        supports = [{'evidence_id': first, 'quote_original': 'SECRET quote'}] if case == 'support' else []
        return parse_generated_answer(json.dumps({'claims': [
            {'text': 'SECRET 계산 조건', 'evidence_ids': [first]},
            {'text': 'SECRET 경로 길이', 'evidence_ids': ids, 'supports': supports},
        ]}))

    service.generator = SimpleNamespace(generate_claims=generate)
    response = service.answer('SECRET 계산 조건과 경로 길이를 함께 설명해줘', context_id='ctx')
    captured = capsys.readouterr()
    assert captured.out == ''
    logs = [json.loads(line) for line in captured.err.splitlines()]
    assert 'SECRET' not in json.dumps(logs, ensure_ascii=False)
    failures = [r for r in logs if r['event'] == 'verification_failure']
    if code is None:
        assert not failures
        assert response.result.status == AnswerStatus.OK
        assert len(response.result.claims) == 2
        assert len(saved) == 1
    else:
        assert len(failures) == 1
        assert failures[0]['reason_code'] == code
        assert failures[0]['round_number'] == 0
        expected_number = 2 if case in ('unknown', 'duplicate', 'empty_refs', 'support') else None
        assert failures[0]['claim_number'] == expected_number
        assert response.result.reason_code == ReasonCode.VERIFICATION_FAILED
        assert response.result.status == AnswerStatus.INSUFFICIENT_EVIDENCE
        assert not saved


@pytest.mark.parametrize('reason', ['UNRESOLVED_PREFIX', 'SUPPORT_NOT_FOUND'])
@pytest.mark.parametrize('outcome', ['fixed', 'unchanged', 'unavailable', 'invalid', 'no_supports', 'unsupported', 'removed'])
def test_prefix_and_missing_quote_repair_or_exclusion(parts, capsys, reason, outcome):
    import json
    from dataclasses import replace
    from unittest.mock import Mock
    service, search, saved, _ = parts
    first = replace(search.items[0], chunk=replace(search.items[0].chunk,
        original_text='fragment ends here. A complete relevant sentence.'))
    lookup = Mock(return_value=ContextSearchResponse('ctx', replace(search, items=(first, search.items[1]))))
    service.search = SimpleNamespace(search=lookup)

    def generate(question, evidence):
        quote = 'fragment ends here.' if reason == 'UNRESOLVED_PREFIX' else 'SECRET invented quote.'
        return GeneratedAnswerDraft((
            GeneratedClaim('SECRET risky claim', (evidence[0].evidence_id,),
                (ClaimSupport(evidence[0].evidence_id, quote),)),
            GeneratedClaim('정상 주장', (evidence[1].evidence_id,),
                (ClaimSupport(evidence[1].evidence_id, evidence[1].original_text),)),
        ))

    def repair(question, evidence, draft, targets):
        assert len(targets) == 1
        assert targets[0].reason_code == reason
        assert targets[0].next_evidence_id is None
        assert targets[0].claim_number == 1
        if outcome == 'unavailable':
            raise GenerationUnavailable('SECRET failure')
        if outcome == 'invalid':
            return GeneratedAnswerDraft((GeneratedClaim('SECRET invalid', ('unknown',)),))
        if outcome == 'no_supports':
            return GeneratedAnswerDraft((GeneratedClaim('SECRET bypass', (evidence[1].evidence_id,)),))
        if outcome == 'removed':
            return GeneratedAnswerDraft(draft.claims[1:])
        if outcome == 'unchanged':
            return draft
        return GeneratedAnswerDraft((GeneratedClaim('수정된 주장', (evidence[0].evidence_id,),
            (ClaimSupport(evidence[0].evidence_id, 'A complete relevant sentence.'),)),))

    provider = SimpleNamespace(generate_claims=Mock(side_effect=generate))
    if outcome != 'unsupported':
        provider.repair_claims = Mock(side_effect=repair)
    service.generator = provider
    response = service.answer('SECRET question', context_id='ctx', top_k=2)
    lookup.assert_called_once()
    provider.generate_claims.assert_called_once()
    if outcome != 'unsupported':
        provider.repair_claims.assert_called_once()
    assert len(saved) == 1
    if outcome == 'fixed':
        assert response.result.status == AnswerStatus.OK
        assert len(response.result.claims) == 2
        assert len(saved[0][1]) == 2
    else:
        assert response.result.status == AnswerStatus.PARTIAL
        assert response.result.reason_code == ReasonCode.EXTRACTION_LIMITED
        assert [c.text for c in response.result.claims] == ['정상 주장']
        assert [e.chunk_id for e in saved[0][1]] == ['c1']
        assert '일부 주장' in response.result.answer_ko
    assert 'SECRET' not in response.result.answer_ko
    captured = capsys.readouterr()
    logs = [json.loads(line) for line in captured.err.splitlines()]
    assert captured.out == ''
    assert 'SECRET' not in json.dumps(logs, ensure_ascii=False)
    excluded = [r for r in logs if r['event'] == 'claim_excluded']
    assert len(excluded) == (0 if outcome in ('fixed', 'removed') else 1)


def test_all_unresolved_claims_are_excluded_without_more_search(parts):
    from unittest.mock import Mock
    service, search, saved, _ = parts
    service.search = SimpleNamespace(search=Mock(return_value=ContextSearchResponse('ctx', search)))
    def generate(question, evidence):
        return GeneratedAnswerDraft(tuple(GeneratedClaim('제외할 주장', (e.evidence_id,),
            (ClaimSupport(e.evidence_id, 'Invented quote.'),)) for e in evidence))
    provider = SimpleNamespace(generate_claims=Mock(side_effect=generate),
                               repair_claims=Mock(side_effect=lambda q, e, d, t: d))
    service.generator = provider
    response = service.answer('질문', context_id='ctx', top_k=2)
    assert response.result.status == AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.result.reason_code == ReasonCode.EXTRACTION_LIMITED
    assert not response.result.claims and not response.result.citations
    assert not saved
    provider.repair_claims.assert_called_once()
    service.search.search.assert_called_once()


@pytest.mark.parametrize('outcome', ['unchanged', 'unavailable', 'whole_draft', 'empty'])
def test_repair_never_rewrites_untargeted_valid_claim(repair_parts, outcome):
    from unittest.mock import Mock
    service, saved, lookup = repair_parts
    original = []

    def generate(question, evidence):
        good = GeneratedClaim('n이 d보다 작을 때 더 빠르다.', tuple(e.evidence_id for e in evidence),
            tuple(ClaimSupport(e.evidence_id, e.original_text) for e in evidence))
        bad = GeneratedClaim('미완결 두 번째 주장', (evidence[0].evidence_id,),
            (ClaimSupport(evidence[0].evidence_id, evidence[0].original_text),))
        original.extend((good, bad))
        return GeneratedAnswerDraft((good, bad))

    def repair(question, evidence, draft, targets):
        assert draft.claims == (original[1],)
        assert targets[0].claim_number == 1
        if outcome == 'unavailable':
            raise GenerationUnavailable('실패')
        if outcome == 'whole_draft':
            return GeneratedAnswerDraft((original[1], original[1]))
        if outcome == 'empty':
            return GeneratedAnswerDraft(())
        return draft

    service.generator.generate_claims.side_effect = generate
    service.generator.repair_claims.side_effect = repair
    response = service.answer('복합 질문', context_id='ctx', top_k=1)
    assert response.result.status == AnswerStatus.PARTIAL
    assert len(response.result.claims) == 1
    assert response.result.claims[0].text == original[0].text
    assert response.result.claims[0].evidence_ids == original[0].evidence_ids
    assert [c.pdf_page for c in response.result.citations] == [6, 7]
    assert '미완결 두 번째 주장' not in response.result.answer_ko
    assert '일부 주장' in response.result.answer_ko
    assert len(saved) == 1 and len(saved[0][1]) == 2
    service.generator.repair_claims.assert_called_once()
    lookup.assert_called_once()
