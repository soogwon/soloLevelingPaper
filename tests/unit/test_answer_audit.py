"""보완 내용과 질문 충족 검사. 외부 호출 없이 응답 처리와 실패 방어를 검증한다."""

import json
from dataclasses import replace

import pytest

from solo_leveling.application.evidence_qa.answer_audit import parse_audit, audit_effects, answer_quotes, audit_schema
from solo_leveling.application.evidence_qa.semantic import SemanticVerificationService
from solo_leveling.domain.evidence_qa import Claim
from solo_leveling.domain.semantic_verification import SemanticVerdict as V, SemanticReason as R
from solo_leveling.infrastructure.generation.fake_semantic_verifier import FakeSemanticVerifier, fixed_audit_checks
from tests.unit.test_answer_service import parts, repair_parts


QUESTION = '계산 조건은? 경로 영향은?'


def set_question(service):
    search = service.search.search
    def lookup(*args, **kwargs):
        found = search(*args, **kwargs)
        return replace(found, result=replace(found.result, query=QUESTION))
    service.search.search = lookup


def audit(claims, groups=(), *, missing=False, preservation='preserved'):
    return {'coverage': [
        {'question_part': '계산 조건은?', 'status': 'answered', 'claim_ids': [claims[0].claim_id],
         'checks': fixed_audit_checks(claims[:1])},
        {'question_part': ' 경로 영향은?', 'status': 'unanswered' if missing else 'answered',
         'claim_ids': [] if missing else [claims[-1].claim_id],
         'checks': fixed_audit_checks(claims[-1:], state='missing' if missing else 'satisfied')}],
        'repairs': [{'source_id': g['source_id'], 'status': preservation,
            'checks': fixed_audit_checks([c for c in claims if c.claim_id in g['claim_ids']], repair=True,
                state='missing' if preservation == 'partial' else preservation)} for g in groups]}


@pytest.mark.parametrize('case', ['unknown_id', 'duplicate_id', 'question_omitted', 'invented_part',
    'unknown_source', 'missing_source', 'duplicate_source', 'empty_covered', 'unanswered_with_ids'])
def test_invalid_audit_is_rejected(case):
    claims = (Claim('c1', '조건', ('e',)), Claim('c2', '영향', ('e',)))
    groups = ({'source_id': '1', 'original_text': '원래 주장', 'claim_ids': ['c1']},)
    raw = audit(claims, groups)
    if case == 'unknown_id':
        raw['coverage'][0]['claim_ids'] = ['없는 번호']
    elif case == 'duplicate_id':
        raw['coverage'][0]['claim_ids'] = ['c1', 'c1']
    elif case == 'question_omitted':
        raw['coverage'].pop()
    elif case == 'invented_part':
        raw['coverage'][0]['question_part'] = '새로운 질문'
    elif case == 'unknown_source':
        raw['repairs'][0]['source_id'] = '2'
    elif case == 'missing_source':
        raw['repairs'] = []
    elif case == 'duplicate_source':
        raw['repairs'] *= 2
    elif case == 'empty_covered':
        raw['coverage'][0]['claim_ids'] = []
    else:
        raw['coverage'][0]['status'] = 'unanswered'
    with pytest.raises(ValueError):
        parse_audit(raw, QUESTION, claims, groups)


def test_semantically_removed_claim_cannot_cover_question():
    claims = (Claim('c1', '조건', ('e',)), Claim('c2', '영향', ('e',)))
    checked = parse_audit(audit(claims), QUESTION, claims, ())
    kept, limited, missing = audit_effects(checked, (), {'c1'})
    assert kept == {'c1'} and not limited
    assert missing == (' 경로 영향은?',)


@pytest.mark.parametrize('missing', [False, True])
def test_question_coverage_controls_final_status_without_extra_calls(parts, missing, capsys):
    service, _, saved, _ = parts
    set_question(service)
    verifier = FakeSemanticVerifier([(V.SUPPORTED, R.FULL_SUPPORT)] * 2)
    def verify_answer(claims, evidence, question, groups):
        assert question == QUESTION and groups == ()
        return verifier.verify(claims, evidence), audit(claims, missing=missing)
    verifier.verify_answer = verify_answer
    service.semantic_verification = SemanticVerificationService(verifier)
    response = service.answer(QUESTION, context_id='ctx').result
    assert response.status.value == ('partial' if missing else 'ok')
    if missing:
        assert response.reason_code.value == 'semantic_limited'
        assert '충분히 답하지 못한 질문 항목: " 경로 영향은?"' in response.answer_ko
    assert len(verifier.calls) == 1 and len(saved) == 1
    logs = capsys.readouterr().err
    assert '계산 조건' not in logs and '경로 영향' not in logs


@pytest.mark.parametrize('preservation', ['preserved', 'partial', 'changed'])
def test_repair_original_is_checked_and_changed_content_is_excluded(repair_parts, preservation):
    service, saved, _ = repair_parts
    set_question(service)
    verifier = FakeSemanticVerifier([(V.SUPPORTED, R.FULL_SUPPORT)])
    def verify_answer(claims, evidence, question, groups):
        assert len(groups) == 1
        assert groups[0]['original_text'] == '최초 불완전 주장'
        assert groups[0]['claim_ids'] == [claims[0].claim_id]
        return verifier.verify(claims, evidence), audit(claims, groups, preservation=preservation)
    verifier.verify_answer = verify_answer
    service.semantic_verification = SemanticVerificationService(verifier)
    result = service.answer(QUESTION, context_id='ctx', top_k=1, pdf_pages=(6, 7)).result
    assert result.status.value == {'preserved': 'ok', 'partial': 'partial',
                                  'changed': 'insufficient_evidence'}[preservation]
    assert len(saved) == (0 if preservation == 'changed' else 1)
    assert len(verifier.calls) == 1


def test_missing_audit_fails_closed_without_retry(parts):
    service, _, saved, _ = parts
    verifier = FakeSemanticVerifier([(V.SUPPORTED, R.FULL_SUPPORT)] * 2)
    verifier.verify_answer = lambda claims, evidence, q, groups: (verifier.verify(claims, evidence), {})
    service.semantic_verification = SemanticVerificationService(verifier)
    result = service.answer(QUESTION, context_id='ctx').result
    assert result.reason_code.value == 'verification_failed'
    assert not saved and len(verifier.calls) == 1


def test_condition_restoration_is_not_treated_as_information_loss():
    claims = (Claim('speed', 'n이 d보다 작을 때 더 빠르다.', ('e',)),
              Claim('path', '경로가 짧을수록 학습하기 쉽다.', ('e',)))
    groups = ({'source_id': '1', 'original_text': '더 빠르다.', 'claim_ids': ['speed']},)
    raw = audit(claims, groups)
    # 실측의 과도한 종합 partial을 재현한다. 원문에 따른 조건 교정은 누락이 아니다.
    raw['repairs'][0]['status'] = 'partial'
    raw['repairs'][0]['checks']['qualifiers']['status'] = 'corrected'
    result = parse_audit(raw, QUESTION, claims, groups)
    retained, limited, missing = audit_effects(result, groups, {'speed', 'path'})
    assert retained == {'speed', 'path'} and not limited and not missing


def test_metric_change_overrides_false_positive_overall_verdict():
    claims = (Claim('speed', '상수 개의 순차 연산을 사용한다.', ('e',)),
              Claim('path', '경로가 짧을수록 학습하기 쉽다.', ('e',)))
    groups = ({'source_id': '1', 'original_text': '계산 복잡도 측면에서 더 빠르다.', 'claim_ids': ['speed']},)
    raw = audit(claims, groups)
    # 종합 판정이 answered/preserved여도 세부 기준 변경은 거부한다.
    raw['coverage'][0]['checks']['requested_relation'] = {'status': 'missing', 'quote_ids': []}
    raw['repairs'][0]['checks']['comparison_basis'] = {'status': 'changed', 'quote_ids': []}
    result = parse_audit(raw, QUESTION, claims, groups)
    retained, limited, missing = audit_effects(result, groups, {'speed', 'path'})
    assert retained == {'path'} and limited and missing == ('계산 조건은?',)


@pytest.mark.parametrize('case', ['invented_quote', 'foreign_claim', 'empty_quote', 'missing_check',
    'topic_not_applicable', 'core_not_applicable', 'bad_quote_type'])
def test_checklist_requires_actual_allowed_answer_quotes(case):
    claims = (Claim('c1', '조건', ('e',)), Claim('c2', '영향', ('e',)))
    groups = ({'source_id': '1', 'original_text': '원래 주장', 'claim_ids': ['c1']},)
    raw = audit(claims, groups)
    check = raw['coverage'][0]['checks']['topic']
    if case == 'invented_quote':
        check['quote_ids'][0] = '존재하지 않는 답변 ID'
    elif case == 'foreign_claim':
        check['quote_ids'][0] = answer_quotes(claims[1:])[0]['quote_id']
    elif case == 'empty_quote':
        check['quote_ids'] = []
    elif case == 'missing_check':
        del raw['coverage'][0]['checks']['topic']
    elif case == 'topic_not_applicable':
        raw['coverage'][0]['checks']['topic'] = {'status': 'not_applicable', 'quote_ids': []}
    elif case == 'core_not_applicable':
        raw['repairs'][0]['checks']['core_fact'] = {'status': 'not_applicable', 'quote_ids': []}
    else:
        check['quote_ids'][0] = ['조건']
    with pytest.raises(ValueError):
        parse_audit(raw, QUESTION, claims, groups)


def test_metric_guard_rejects_live_false_positive_even_when_all_model_checks_pass():
    from solo_leveling.domain.semantic_verification import VerificationDetail as D
    question = '계산 복잡도가 더 낮은 조건은? 경로 영향은?'
    claims = (Claim('c1', '상수 개의 순차 연산을 사용한다.', ('e',)),
              Claim('c2', '경로가 짧을수록 학습하기 쉽다.', ('e',)))
    groups = ({'source_id': '1', 'original_text': '계산 복잡도 측면에서 더 빠르다.', 'claim_ids': ['c1']},)
    raw = audit(claims, groups)
    raw['coverage'][0]['question_part'] = '계산 복잡도가 더 낮은 조건은?'
    checked = parse_audit(raw, question, claims, groups)
    assert checked.coverage[0].status == 'partial'
    assert checked.repairs[0].status == 'changed'
    assert checked.detail_codes == (D.METRIC_SUBSTITUTION,)
    kept, limited, missing = audit_effects(checked, groups, {'c1', 'c2'})
    assert kept == {'c2'} and limited and missing == ('계산 복잡도가 더 낮은 조건은?',)


@pytest.mark.parametrize('expected,actual,blocked', [
    ('계산 복잡도 조건', ['상수 개 순차 연산'], True),
    ('computational complexity', ['O(n) sequential operations'], True),
    ('complexity per layer', ['constant sequentially executed operations'], True),
    ('총 계산량', ['순차적으로 실행되는 연산이 적다'], True),
    ('계산 복잡도 조건', ['n<d일 때 계산 복잡도가 낮다'], False),
    ('계산 복잡도와 순차 연산', ['순차 연산 수'], False),
    ('순차 연산 수', ['상수 개의 순차 연산'], False),
    ('계산 복잡도', ['총 계산량이 적다', '상수 개 순차 연산'], False),
    ('계산 복잡도', ['경로가 짧다'], False),
])
def test_metric_guard_is_narrow_and_does_not_approve_unknown_cases(expected, actual, blocked):
    from solo_leveling.application.evidence_qa.answer_audit import metric_substitution
    assert metric_substitution(expected, actual) is blocked


def test_quote_rejection_code_survives_default_log_without_body(parts):
    from contextlib import redirect_stderr
    from io import StringIO
    from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
    service, _, saved, _ = parts
    set_question(service)
    verifier = FakeSemanticVerifier([(V.SUPPORTED, R.FULL_SUPPORT)] * 2)
    def verify_answer(claims, evidence, question, groups):
        raw = audit(claims, groups)
        raw['coverage'][0]['checks']['topic']['quote_ids'][0] = '비공개-가짜-구절'
        return verifier.verify(claims, evidence), raw
    verifier.verify_answer = verify_answer
    service.semantic_verification = SemanticVerificationService(verifier)
    output = StringIO()
    with redirect_stderr(SafeDiagnosticStream(output)):
        response = service.answer(QUESTION, context_id='ctx')
    assert response.result.reason_code.value == 'verification_failed'
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    detail, = [r for r in records if r.get('stage') == 'semantic_diagnostic' and r.get('event') == 'detail']
    assert detail['reason_code'] == 'ANSWER_QUOTE_ID_UNKNOWN'
    assert '비공개' not in output.getvalue() and '계산 조건' not in output.getvalue()
    assert not saved and len(verifier.calls) == 1


@pytest.mark.parametrize('bad_field,bad_value', [
    ('reason_code', '비공개 원문'), ('quote_original', '비공개 원문'),
    ('claim_id', 'private-id'), ('elapsed_ms', True), ('request_id', 'private-request'),
    ('check_item', '비공개 질문'), ('check_item', ['coverage.topic']),
])
def test_detail_log_rejects_arbitrary_fields_and_values(bad_field, bad_value):
    from io import StringIO
    from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
    record = {'request_id': 'a' * 32, 'span_id': 'b' * 32, 'stage': 'semantic_diagnostic',
              'event': 'detail', 'elapsed_ms': 0, 'error_code': None, 'reason_code': 'QUOTE_REQUIRED'}
    record[bad_field] = bad_value
    output = StringIO()
    SafeDiagnosticStream(output).write(json.dumps(record) + '\n')
    assert output.getvalue() == ''


def test_quote_catalog_preserves_exact_spans_and_full_conditional_claim():
    text = 'n < d일 때 더 빠르다.\n단, 3.14라는 값은 예시다.'
    claims = (Claim('one', text, ('e',)), Claim('two', '다른 주장.', ('e',)))
    catalog = answer_quotes(claims)
    assert catalog == answer_quotes(claims)
    assert catalog[0]['text'] == text
    assert len({q['quote_id'] for q in catalog}) == len(catalog)
    originals = {c.claim_id: c.text for c in claims}
    for quote in catalog:
        assert quote['text'] == originals[quote['claim_id']][quote['start']:quote['end']]
    assert any(q['text'] == '단, 3.14라는 값은 예시다.' for q in catalog)
    assert answer_quotes((replace(claims[0], text=text + ' 변경'),))[0]['quote_id'] != catalog[0]['quote_id']


@pytest.mark.parametrize('case,expected', [
    ('unknown', 'ANSWER_QUOTE_ID_UNKNOWN'), ('stale', 'ANSWER_QUOTE_ID_UNKNOWN'),
    ('duplicate', 'ANSWER_QUOTE_ID_DUPLICATE'), ('foreign', 'ANSWER_QUOTE_REFERENCE_INVALID'),
    ('legacy', 'CHECK_FORMAT_INVALID'),
])
def test_id_selection_rejects_unknown_stale_foreign_duplicate_and_rewritten_text(case, expected):
    from solo_leveling.domain.semantic_verification import AuditValidationError
    claims = (Claim('c1', '조건', ('e',)), Claim('c2', '영향', ('e',)))
    raw = audit(claims)
    check = raw['coverage'][0]['checks']['topic']
    if case == 'unknown':
        check['quote_ids'] = ['made-up']
    elif case == 'stale':
        check['quote_ids'] = [answer_quotes((replace(claims[0], text='이전 내용'),))[0]['quote_id']]
    elif case == 'duplicate':
        check['quote_ids'] *= 2
    elif case == 'foreign':
        check['quote_ids'] = [answer_quotes(claims[1:])[0]['quote_id']]
    else:
        check['quotes'] = [{'claim_id': 'c1', 'quote_claim': '새로 작성한 구절'}]
        del check['quote_ids']
    with pytest.raises(AuditValidationError) as error:
        parse_audit(raw, QUESTION, claims, ())
    assert error.value.detail.value == expected


def test_quote_schema_is_request_scoped_without_mutating_shared_template():
    from solo_leveling.application.evidence_qa.answer_audit import AUDIT_SCHEMA
    a = (Claim('a', '첫 주장.', ('e',)),)
    b = (Claim('b', '다른 주장.', ('e',)),)
    for claims in (a, b):
        schema = audit_schema(claims)
        expected = [q['quote_id'] for q in answer_quotes(claims)]
        for group in ('coverage', 'repairs'):
            item = schema['properties'][group]['items']
            if group == 'coverage':
                item = item['anyOf'][0]
            for check in item['properties']['checks']['properties'].values():
                positive, negative = check['anyOf']
                assert positive['properties']['quote_ids']['items']['enum'] == expected
                assert positive['properties']['quote_ids']['minItems'] == 1
                assert negative['properties']['quote_ids']['maxItems'] == 0
                assert set(positive['properties']['status']['enum']) == (
                    {'satisfied'} if group == 'coverage' else {'preserved', 'corrected'})
                assert not set(positive['properties']['status']['enum']) & set(negative['properties']['status']['enum'])
    base = AUDIT_SCHEMA['properties']['coverage']['items']['properties']['checks']['properties']['topic']
    assert 'enum' not in base['properties']['quote_ids']['items']


@pytest.mark.parametrize('group,name', [
    ('coverage', 'topic'), ('coverage', 'requested_relation'), ('coverage', 'qualifiers'),
    ('repairs', 'core_fact'), ('repairs', 'comparison_basis'), ('repairs', 'qualifiers'),
])
@pytest.mark.parametrize('code', ['QUOTE_REQUIRED', 'ANSWER_QUOTE_REFERENCE_INVALID'])
def test_required_quote_location_reaches_safe_log(parts, group, name, code):
    from contextlib import redirect_stderr
    from io import StringIO
    from solo_leveling.diagnostics import semantic_diagnostic, traced
    from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
    claims = (Claim('c1', '비공개 주장', ('e',)), Claim('c2', '다른 비공개 주장', ('e',)))
    groups = ({'source_id': '1', 'original_text': '비공개 초안', 'claim_ids': ['c1']},)
    verifier = FakeSemanticVerifier([(V.SUPPORTED, R.FULL_SUPPORT)] * 2)
    def verify_answer(claims, evidence, question, groups):
        raw = audit(claims, groups)
        raw[group][0]['checks'][name]['quote_ids'] = ([] if code == 'QUOTE_REQUIRED' else
            [answer_quotes(claims[1:])[0]['quote_id']])
        return verifier.verify(claims, evidence), raw
    verifier.verify_answer = verify_answer
    report = SemanticVerificationService(verifier).check(claims, (), question=QUESTION, repair_groups=groups)
    assert report.detail_code.value == code
    assert report.detail_check == f'{group}.{name}'
    assert report.failure.value == 'invalid_response'
    output = StringIO()
    with redirect_stderr(SafeDiagnosticStream(output)):
        traced('ask_paper', request=True)(semantic_diagnostic)(report)
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    detail, = [r for r in records if r.get('stage') == 'semantic_diagnostic' and r.get('event') == 'detail']
    assert detail['check_item'] == f'{group}.{name}'
    assert detail['reason_code'] == code
    assert '비공개' not in output.getvalue() and '계산 조건' not in output.getvalue()


def test_unanswered_checks_have_no_positive_quote_requirement():
    claims = (Claim('c1', '계산 조건 답변', ('e',)),)
    parsed = parse_audit(audit(claims, missing=True), QUESTION, claims, ())
    kept, limited, missing = audit_effects(parsed, (), {'c1'})
    assert kept == {'c1'} and missing == (' 경로 영향은?',)
    assert parsed.coverage[1].status == 'unanswered'


def test_empty_repair_retains_other_valid_answer(repair_parts):
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim, ClaimSupport
    service, saved, lookup = repair_parts
    found = lookup.return_value
    first = found.result.items[0]
    lookup.return_value = replace(found, result=replace(found.result, items=(replace(first,
        chunk=replace(first.chunk, original_text='Shorter paths help learning. Faster when the sequence\n6')),)))
    set_question(service)
    def generate(question, evidence):
        item = evidence[0]
        return GeneratedAnswerDraft((
            GeneratedClaim('불완전 계산 조건', (item.evidence_id,),
                (ClaimSupport(item.evidence_id, 'Faster when the sequence\n6'),)),
            GeneratedClaim('경로가 짧을수록 학습에 유리하다.', (item.evidence_id,),
                (ClaimSupport(item.evidence_id, 'Shorter paths help learning.'),)),
        ))
    service.generator.generate_claims.side_effect = generate
    service.generator.repair_claims.side_effect = lambda *args: GeneratedAnswerDraft(())
    verifier = FakeSemanticVerifier([(V.SUPPORTED, R.FULL_SUPPORT)])
    def verify_answer(claims, evidence, question, groups):
        assert groups == ()
        assert len(claims) == 1 and '경로' in claims[0].text
        raw = audit(claims)
        raw['coverage'][0].update(status='unanswered', claim_ids=[],
            checks=fixed_audit_checks((), state='missing'))
        return verifier.verify(claims, evidence), raw
    verifier.verify_answer = verify_answer
    service.semantic_verification = SemanticVerificationService(verifier)
    response = service.answer(QUESTION, context_id='ctx', top_k=1, pdf_pages=(6, 7))
    result = response.result
    assert result.status.value == 'partial' and response.verification_level.value == 'model_checked'
    assert len(result.claims) == 1 and '경로' in result.claims[0].text
    assert len(saved) == 1 and len(verifier.calls) == 1
    service.generator.repair_claims.assert_called_once()


def test_model_cannot_invent_review_for_server_owned_empty_group():
    claims = (Claim('c1', '정상 답변', ('e',)),)
    groups = ({'source_id': 'empty', 'original_text': '사라진 보완', 'claim_ids': []},)
    verifier = FakeSemanticVerifier([(V.SUPPORTED, R.FULL_SUPPORT)])
    def verify_answer(claims, evidence, question, active):
        assert active == ()
        raw = audit(claims)
        raw['repairs'] = [{'source_id': 'empty', 'status': 'preserved',
                          'checks': fixed_audit_checks(claims, repair=True)}]
        return verifier.verify(claims, evidence), raw
    verifier.verify_answer = verify_answer
    report = SemanticVerificationService(verifier).check(claims, (), question=QUESTION, repair_groups=groups)
    assert report.detail_code.value == 'REPAIR_REFERENCE_INVALID'


@pytest.mark.parametrize('text,blocked', [
    ('Self-attention은 계산 복잡도 측면에서, 시퀀스 길이가 n일 때 recurrent 층보다 더 빠르다.', True),
    ('시퀀스 길이 n에 대해 recurrent 층보다 더 빠르다.', True),
    ('크기가 m인 경우 더 빠르다.', True),
    ('It is faster when the sequence length is n.', True),
    ('시퀀스 길이 n이 표현 차원 d보다 작을 때 더 빠르다.', False),
    ('n < d일 때 더 빠르다.', False),
    ('시퀀스 길이가 n일 때, n < d이면 더 빠르다.', False),
    ('시퀀스 길이가 6일 때 더 빠르다.', False),
    ('경로가 짧을수록 학습하기 쉽다.', False),
    ('시퀀스 길이가 n일 때 더 빠르다. 다른 항목은 x < y이다.', True),
])
def test_variable_only_condition_guard_is_narrow(text, blocked):
    from solo_leveling.application.evidence_qa.answer_audit import variable_only_condition
    assert variable_only_condition('더 빠른 조건은?', text) is blocked
    assert not variable_only_condition('변수 표기를 설명해줘.', text)


@pytest.mark.parametrize('normal', [False, True])
@pytest.mark.parametrize('keep_path', [False, True])
def test_false_supported_variable_condition_is_removed_from_final_answer(parts, normal, keep_path):
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim, ClaimSupport
    service, _, saved, _ = parts
    set_question(service)
    speed = ('시퀀스 길이 n이 표현 차원 d보다 작을 때 recurrent 층보다 더 빠르다.' if normal else
             'Self-attention은 계산 복잡도 측면에서, 시퀀스 길이가 n일 때 recurrent 층보다 더 빠르다.')
    def generate(question, evidence):
        texts = [speed] + (['경로가 짧을수록 학습하기 쉽다.'] if keep_path else [])
        return GeneratedAnswerDraft(tuple(GeneratedClaim(text, (e.evidence_id,),
            (ClaimSupport(e.evidence_id, e.original_text),)) for text, e in zip(texts, evidence)))
    service.generator.generate_claims = generate
    verifier = FakeSemanticVerifier([(V.SUPPORTED, R.FULL_SUPPORT)] * (2 if keep_path else 1))
    def verify_answer(claims, evidence, question, groups):
        return verifier.verify(claims, evidence), audit(claims, missing=not keep_path)
    verifier.verify_answer = verify_answer
    service.semantic_verification = SemanticVerificationService(verifier)
    response = service.answer(QUESTION, context_id='ctx')
    expected = 'ok' if normal and keep_path else 'partial' if normal or keep_path else 'insufficient_evidence'
    assert response.result.status.value == expected
    assert response.verification_level.value == 'model_checked'
    assert (speed in [c.text for c in response.result.claims]) is normal
    assert len(response.result.claims) == int(normal) + int(keep_path)
    assert len(saved) == int(normal or keep_path)
    assert len(verifier.calls) == 1


def test_variable_condition_detail_log_contains_only_code(parts):
    from contextlib import redirect_stderr
    from io import StringIO
    from solo_leveling.diagnostics import traced
    from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
    claim = Claim('private', '시퀀스 길이가 n일 때 더 빠르다.', ('e',))
    verifier = FakeSemanticVerifier([(V.SUPPORTED, R.FULL_SUPPORT)])
    verifier.verify_answer = lambda claims, evidence, question, groups: (
        verifier.verify(claims, evidence), audit(claims, missing=True))
    output = StringIO()
    with redirect_stderr(SafeDiagnosticStream(output)):
        report = traced('ask_paper', request=True)(SemanticVerificationService(verifier).check)(
            (claim,), (), question=QUESTION)
    assert report.judgments[0].reason == R.MISSING_CONDITION
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    detail, = [r for r in records if r.get('stage') == 'semantic_diagnostic' and r.get('event') == 'detail']
    assert detail['reason_code'] == 'VARIABLE_ONLY_CONDITION'
    assert '길이' not in output.getvalue() and 'private' not in output.getvalue()


def test_coverage_schema_links_status_to_references():
    claims = (Claim('c1', '주장', ('e',)),)
    branches = audit_schema(claims)['properties']['coverage']['items']['anyOf']
    assert len(branches) == 3
    for branch in branches:
        props = branch['properties']
        assert props['claim_ids']['items']['enum'] == ['c1']
        if props['status']['enum'] == ['unanswered']:
            assert props['claim_ids']['maxItems'] == 0
            for check in props['checks']['properties'].values():
                for option in check['anyOf']:
                    assert 'satisfied' not in option['properties']['status']['enum']
                    assert option['properties']['quote_ids']['maxItems'] == 0
        else:
            assert props['claim_ids']['minItems'] == 1
    empty = audit_schema(())['properties']['coverage']['items']['anyOf']
    assert len(empty) == 1 and empty[0]['properties']['status']['enum'] == ['unanswered']


@pytest.mark.parametrize('mutation,reason', [
    ({'extra': '비공개'}, 'ITEM_FIELDS_INVALID'),
    ({'question_part': ''}, 'QUESTION_PART_INVALID'),
    ({'status': '비공개'}, 'STATUS_INVALID'),
    ({'claim_ids': '비공개'}, 'CLAIM_IDS_TYPE_INVALID'),
    ({'claim_ids': [{}]}, 'CLAIM_ID_TYPE_INVALID'),
    ({'claim_ids': ['비공개']}, 'UNKNOWN_CLAIM_ID'),
    ({'claim_ids': ['c1', 'c1']}, 'DUPLICATE_CLAIM_ID'),
    ({'status': 'unanswered'}, 'UNANSWERED_HAS_CLAIM_IDS'),
    ({'claim_ids': []}, 'ANSWERED_WITHOUT_CLAIM_IDS'),
    ({'status': 'partial', 'claim_ids': []}, 'PARTIAL_WITHOUT_CLAIM_IDS'),
])
def test_coverage_rejection_subreason_reaches_safe_log(mutation, reason):
    from contextlib import redirect_stderr
    from io import StringIO
    from solo_leveling.diagnostics import traced
    from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
    claims = (Claim('c1', '비공개 주장', ('e',)),)
    verifier = FakeSemanticVerifier([(V.SUPPORTED, R.FULL_SUPPORT)])
    def verify_answer(claims, evidence, question, groups):
        raw = audit(claims)
        raw['coverage'][0].update(mutation)
        return verifier.verify(claims, evidence), raw
    verifier.verify_answer = verify_answer
    output = StringIO()
    with redirect_stderr(SafeDiagnosticStream(output)):
        report = traced('ask_paper', request=True)(SemanticVerificationService(verifier).check)(
            claims, (), question=QUESTION)
    assert report.failure.value == 'invalid_response'
    assert report.detail_code.value == 'COVERAGE_REFERENCE_INVALID'
    assert report.detail_subreason == reason
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    detail, = [r for r in records if r.get('stage') == 'semantic_diagnostic']
    assert detail['rejection_reason'] == reason
    assert '비공개' not in output.getvalue() and '계산 조건' not in output.getvalue()
    assert len(verifier.calls) == 1


@pytest.mark.parametrize('verdict,reason', [(V.PARTIALLY_SUPPORTED, R.MISSING_CONDITION),
                                          (V.SUPPORTED, R.FULL_SUPPORT)])
def test_actual_variable_case_all_unanswered_is_normal_insufficient(parts, verdict, reason):
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim, ClaimSupport
    service, _, saved, _ = parts
    set_question(service)
    def generate(question, evidence):
        e = evidence[0]
        return GeneratedAnswerDraft((GeneratedClaim(
            'Self-attention은 계산 복잡도 측면에서, 시퀀스 길이가 n일 때 recurrent 층보다 더 빠르다.',
            (e.evidence_id,), (ClaimSupport(e.evidence_id, e.original_text),)),))
    service.generator.generate_claims = generate
    verifier = FakeSemanticVerifier([(verdict, reason)])
    def verify_answer(claims, evidence, question, groups):
        raw = audit(claims, missing=True)
        raw['coverage'][0].update(status='unanswered', claim_ids=[],
                                  checks=fixed_audit_checks((), state='missing'))
        return verifier.verify(claims, evidence), raw
    verifier.verify_answer = verify_answer
    service.semantic_verification = SemanticVerificationService(verifier)
    response = service.answer(QUESTION, context_id='ctx')
    assert response.result.status.value == 'insufficient_evidence'
    assert response.result.reason_code.value == 'semantic_limited'
    assert response.verification_level.value == 'model_checked'
    assert not response.result.claims and not response.result.citations and not saved
    assert len(verifier.calls) == 1


@pytest.mark.parametrize('reason,code', [('비공개', 'COVERAGE_REFERENCE_INVALID'),
                                      ([], 'COVERAGE_REFERENCE_INVALID'),
                                      ('UNKNOWN_CLAIM_ID', 'QUOTE_REQUIRED')])
def test_coverage_subreason_log_allowlist(reason, code):
    from io import StringIO
    from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
    record = {'request_id': 'a' * 32, 'span_id': 'b' * 32, 'stage': 'semantic_diagnostic',
              'event': 'detail', 'elapsed_ms': 0, 'error_code': None,
              'reason_code': code, 'rejection_reason': reason}
    output = StringIO()
    SafeDiagnosticStream(output).write(json.dumps(record) + '\n')
    assert not output.getvalue()
