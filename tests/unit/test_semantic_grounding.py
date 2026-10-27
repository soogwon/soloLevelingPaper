"""실제 실패 구절을 고정하고 항목 검사·인용 범위 검증을 외부 호출 없이 확인한다.

모의 응답 테스트는 모델 정확성을 증명하지 않는다. 실제 호출 평가는 별도로 수행한다.
"""

from copy import deepcopy
import json

import pytest

from solo_leveling.domain.evidence_qa import Claim, EvidenceInput
from solo_leveling.infrastructure.generation.semantic_verifier import _checked_judgment
from solo_leveling.application.evidence_qa.semantic import SemanticVerificationService
from tests.unit.test_semantic_provider import provider, checked_response, original_id
from tests.unit.test_openai_generator import envelope


# Attention Is All You Need, PDF 6페이지의 저장된 인용 원문. 개인 식별자는 포함하지 않는다.
PATH_EVIDENCE = (
    'as measured by the minimum number of sequential operations required.\n'
    'The third is the path length between long-range dependencies in the network. Learning long-range\n'
    'dependencies is a key challenge in many sequence transduction tasks. One key factor affecting the\n'
    'ability to learn such dependencies is the length of the paths forward and backward signals have to\n'
    'traverse in the network.'
)
PATH_CLAIM = ('최대 경로 길이가 짧을수록 장거리 의존성을 학습하기 유리하며, '
              '장거리 의존성을 학습하는 능력에는 네트워크에서 순전파 및 역전파 신호가 '
              '이동해야 하는 경로의 길이가 핵심적으로 영향을 미친다.')
PATH_QUOTE = ('One key factor affecting the ability to learn such dependencies is the length '
              'of the paths forward and backward signals have to traverse in the network.')


def test_actual_direction_failure_cannot_override_unsupported_check():
    claim = Claim('cl1', PATH_CLAIM, ('e1',))
    evidence = EvidenceInput('e1', 'sample-path', '시험용 번역', PATH_EVIDENCE, None, 6)
    item = checked_response()
    item['checks']['content']['quote_ids'] = [original_id('e1', PATH_EVIDENCE)]
    item['checks']['direction'] = {'status': 'unsupported', 'quote_ids': []}
    # 최종 판정이 잘못 supported여도 항목별 미충족 결과를 우선한다.
    judgment = _checked_judgment(item, (claim,), (evidence,))
    assert judgment.verdict.value == 'partially_supported'
    assert judgment.reason.value == 'unsupported_direction'


@pytest.mark.parametrize('name,reason', [
    ('content', 'partial_support'), ('direction', 'unsupported_direction'),
    ('conditions', 'missing_condition'), ('causality', 'unsupported_causality'),
    ('metrics', 'metric_confusion'),
])
def test_unsupported_check_downgrades_supported_provider_response(provider, name, reason):
    verifier, claims, evidence, _, response = provider
    item = checked_response()
    item['checks'][name] = {'status': 'unsupported', 'quote_ids': []}
    response.json.return_value = envelope(json.dumps({'judgments': [item]}))
    report = SemanticVerificationService(verifier).check(claims, evidence)
    assert report.failure is None
    assert report.judgments[0].verdict.value == 'partially_supported'
    assert report.judgments[0].reason.value == reason


@pytest.mark.parametrize('bad', [
    'invented', 'uncited', 'missing_quotes', 'empty_quote', 'missing_check',
    'extra_field', 'bad_status', 'content_not_applicable', 'not_applicable_with_quote',
    'merged_quote', 'too_many_quotes',
])
def test_invalid_check_fails_closed_without_retry(provider, bad):
    verifier, claims, evidence, post, response = provider
    item = checked_response()
    content = item['checks']['content']
    if bad == 'invented':
        content['quote_ids'][0] = original_id('e1', 'Shorter paths improve learning.')
    elif bad == 'uncited':
        content['quote_ids'] = [original_id('unused', 'Not cited.')]
    elif bad == 'missing_quotes':
        content['quote_ids'] = []
    elif bad == 'empty_quote':
        content['quote_ids'][0] = '  '
    elif bad == 'missing_check':
        del item['checks']['direction']
    elif bad == 'extra_field':
        content['explanation'] = 'PRIVATE'
    elif bad == 'bad_status':
        content['status'] = 'PRIVATE'
    elif bad == 'content_not_applicable':
        item['checks']['content'] = {'status': 'not_applicable', 'quote_ids': []}
    elif bad == 'not_applicable_with_quote':
        content['status'] = 'not_applicable'
    elif bad == 'merged_quote':
        content['quote_ids'][0] = original_id('e1', 'Faster when the sequence length n is smaller than d.')
    elif bad == 'too_many_quotes':
        content['quote_ids'] *= 5
    response.json.return_value = envelope(json.dumps({'judgments': [item]}))
    report = SemanticVerificationService(verifier).check(claims, evidence)
    assert report.failure.value == 'invalid_response'
    assert not report.judgments
    post.assert_called_once()


def test_multiple_cited_chunks_are_kept_separate(provider):
    verifier, claims, evidence, _, response = provider
    item = checked_response()
    item['checks']['content']['quote_ids'].append(original_id('e2', 'length n is smaller than d.'))
    item['checks']['conditions'] = deepcopy(item['checks']['content'])
    response.json.return_value = envelope(json.dumps({'judgments': [item]}))
    report = SemanticVerificationService(verifier).check(claims, evidence)
    assert report.failure is None
    assert report.judgments[0].verdict.value == 'supported'


def test_check_quotes_are_not_recorded_in_diagnostics(provider, capsys):
    from solo_leveling.diagnostics import traced
    verifier, claims, evidence, _, _ = provider
    @traced('answer', request=True)
    def run():
        return SemanticVerificationService(verifier).check(claims, evidence)
    assert run().failure is None
    logs = capsys.readouterr().err
    assert 'Faster when the sequence' not in logs
    assert 'checks' not in logs and 'quote_original' not in logs


def test_original_catalog_keeps_exact_text_and_page_boundaries(provider):
    from dataclasses import replace
    from solo_leveling.application.evidence_qa.original_quotes import original_quotes
    _, claims, evidence, _, _ = provider
    text = 'First 3.14 value.\nSecond line\ncontinues here.'
    evidence = (replace(evidence[0], original_text=text), *evidence[1:])
    catalog = original_quotes(claims, evidence)
    assert catalog == original_quotes(claims, evidence)
    assert catalog[0]['text'] == text
    assert len({q['quote_id'] for q in catalog}) == len(catalog)
    sources = {e.evidence_id: e for e in evidence}
    for q in catalog:
        assert q['evidence_id'] != 'unused'
        source = sources[q['evidence_id']]
        assert q['text'] == source.original_text[q['start']:q['end']]
        assert q['pdf_page'] == source.pdf_page
    assert any(q['text'] == 'Second line\ncontinues here.' for q in catalog)
    assert original_id('e1', text) != original_id('e1', text + ' Changed.')


@pytest.mark.parametrize('name', ['content', 'direction', 'conditions', 'causality', 'metrics'])
@pytest.mark.parametrize('bad,code', [
    ('foreign', 'ORIGINAL_QUOTE_REFERENCE_INVALID'),
    ('stale', 'ORIGINAL_QUOTE_ID_UNKNOWN'),
    ('duplicate', 'ORIGINAL_QUOTE_ID_DUPLICATE'),
    ('empty', 'ORIGINAL_QUOTE_POLICY_INVALID'),
    ('legacy', 'CLAIM_CHECK_INVALID'),
])
def test_original_failure_check_location_is_safe_and_preserved(provider, name, bad, code):
    from contextlib import redirect_stderr
    from io import StringIO
    from solo_leveling.diagnostics import traced
    from solo_leveling.interfaces.cli.safe_diagnostic_stream import SafeDiagnosticStream
    verifier, claims, evidence, post, response = provider
    # 전체 요청에 존재하지만 첫 주장에서는 인용하지 않은 근거를 만든다.
    claims = (*claims, Claim('cl2', '별도 주장', ('unused',)))
    item = checked_response()
    item['checks'][name] = {'status': 'supported', 'quote_ids': [original_id()]}
    check = item['checks'][name]
    if bad == 'foreign':
        check['quote_ids'] = [original_id('unused', 'Not cited.')]
    elif bad == 'stale':
        check['quote_ids'] = [original_id('e1', '이전 비공개 원문')]
    elif bad == 'duplicate':
        check['quote_ids'] *= 2
    elif bad == 'empty':
        check['quote_ids'] = []
    else:
        check.pop('quote_ids')
        check['quotes'] = [{'evidence_id': 'e1', 'quote_original': '비공개 재작성 구절'}]
    response.json.return_value = envelope(json.dumps({'judgments': [item]}))
    output = StringIO()
    with redirect_stderr(SafeDiagnosticStream(output)):
        report = traced('ask_paper', request=True)(SemanticVerificationService(verifier).check)(claims, evidence)
    assert report.failure.value == 'invalid_response' and not report.judgments
    assert report.detail_code.value == code and report.detail_check == f'original.{name}'
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    detail, = [r for r in records if r.get('stage') == 'semantic_diagnostic']
    assert detail['reason_code'] == code and detail['check_item'] == f'original.{name}'
    assert '비공개' not in output.getvalue() and 'Faster' not in output.getvalue()
    post.assert_called_once()


def test_catalog_is_in_payload_schema_and_input_limit(provider):
    from solo_leveling.application.evidence_qa.semantic import verification_payload
    from solo_leveling.application.evidence_qa.original_quotes import original_quotes
    verifier, claims, evidence, post, _ = provider
    payload = verification_payload(claims, evidence)
    without_catalog = dict(payload)
    without_catalog.pop('original_quotes')
    limit = len(json.dumps(without_catalog, ensure_ascii=False))
    assert SemanticVerificationService(verifier, max_input_chars=limit).check(
        claims, evidence).failure.value == 'input_limit'
    post.assert_not_called()
    assert SemanticVerificationService(verifier).check(claims, evidence).failure is None
    sent = post.call_args.kwargs['json']
    data = json.loads(sent['input'][0]['content'])
    assert data['original_quotes'] == original_quotes(claims, evidence)
    expected = [q['quote_id'] for q in data['original_quotes']]
    for name, node in sent['text']['format']['schema']['$defs']['checks']['properties'].items():
        for branch in node['anyOf']:
            props = branch['properties']
            assert props['quote_ids']['items']['enum'] == expected
            if props['status']['enum'] == ['supported']:
                assert props['quote_ids']['minItems'] == 1
            if props['status']['enum'] == ['not_applicable']:
                assert name != 'content' and props['quote_ids']['maxItems'] == 0
