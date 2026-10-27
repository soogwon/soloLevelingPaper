"""네트워크를 대체하여 OpenAI 요청·응답과 실패 정책을 검증한다."""

import json
from unittest.mock import Mock

import pytest
import requests

from solo_leveling.application.evidence_qa.ports import GenerationUnavailable
from solo_leveling.application.evidence_qa.response_parser import GenerationFormatError
from solo_leveling.domain.evidence_qa import EvidenceInput
from solo_leveling.infrastructure.generation.openai_generator import (
    FailureKind, OpenAIClaimGenerator, OpenAIGenerationError, OpenAIGenerationSettings,
)


def envelope(text='{"claims": []}'):
    return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
        'status': 'completed', 'content': [{'type': 'output_text', 'text': text}]}]}


@pytest.fixture
def setup(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = envelope()
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, 'post', post)
    generator = OpenAIClaimGenerator(OpenAIGenerationSettings('test-only-key', allow_external_api=True))
    evidence = (EvidenceInput('ev-1', 'internal-chunk', '근거 본문', 'Original.', None, 1),)
    return generator, evidence, post, response


def test_request_contract_and_normal_conversion(setup):
    generator, evidence, post, response = setup
    response.json.return_value = envelope(json.dumps({'claims': [
        {'text': '주장', 'evidence_ids': ['ev-1'],
         'supports': [{'evidence_id': 'ev-1', 'quote_original': 'Original.'}]},
    ]}))
    result = generator.generate_claims('질문', evidence)
    assert result.claims[0].evidence_ids == ('ev-1',)
    assert result.claims[0].supports[0].quote_original == 'Original.'
    args, kwargs = post.call_args
    assert args == ('https://api.openai.com/v1/responses',)
    assert kwargs['allow_redirects'] is False
    assert kwargs['timeout'] == (5.0, 30.0)
    payload = kwargs['json']
    assert payload['model'] == 'gpt-5.4-mini'
    assert payload['store'] is False
    assert '답변 보류 안내문은 서버가 작성한다' in payload['instructions']
    assert '근거에 명시된 한계나 부정적 결과' in payload['instructions']
    assert payload['max_output_tokens'] == 2048
    assert payload['text']['format']['strict'] is True
    assert payload['text']['format']['schema']['additionalProperties'] is False
    claim_schema = payload['text']['format']['schema']['properties']['claims']['items']
    assert 'supports' in claim_schema['required']
    assert claim_schema['properties']['supports']['items']['additionalProperties'] is False
    assert '원문 문장 전체' in payload['instructions']
    data = json.loads(payload['input'][0]['content'])
    assert data['question'] == '질문'
    assert data['evidence'][0]['evidence_id'] == 'ev-1'
    assert data['evidence'][0]['starts_mid_sentence'] is False
    assert data['evidence'][0]['ends_mid_sentence'] is False
    assert data['evidence'][0]['follows_evidence_id'] is None
    assert 'original_text를 기준으로 판단하라' in payload['instructions']
    assert 'internal-chunk' not in json.dumps(payload)
    assert 'test-only-key' not in repr(generator.settings)
    response.close.assert_called_once()


def test_continuation_relation_is_sent_to_provider(setup):
    generator, _, post, _ = setup
    evidence = (
        EvidenceInput('first', 'c1', '조건 앞', 'Faster when', None, 6, False, True),
        EvidenceInput('next', 'c2', '조건 뒤', 'the sequence is short.', None, 7,
                      True, False, 'first'),
    )
    generator.generate_claims('조건은?', evidence)
    payload = post.call_args.kwargs['json']
    supplied = json.loads(payload['input'][0]['content'])['evidence']
    assert supplied[1]['follows_evidence_id'] == 'first'
    assert '해당 근거 ID들을 모두 인용하라' in payload['instructions']


@pytest.mark.parametrize('repair', [False, True])
def test_candidate_ids_constrain_both_reference_fields_per_request(setup, repair):
    from dataclasses import replace
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft
    from solo_leveling.infrastructure.generation.openai_generator import _SCHEMA
    generator, evidence, post, _ = setup

    def invoke(items):
        if repair:
            generator.repair_claims('질문', items, GeneratedAnswerDraft(()), ())
        else:
            generator.generate_claims('질문', items)
        return post.call_args.kwargs['json']['text']['format']['schema']

    first = invoke(evidence + (replace(evidence[0], evidence_id='ev-2'),))
    second = invoke((replace(evidence[0], evidence_id='new-only'),))
    for schema, expected in ((first, ['ev-1', 'ev-2']), (second, ['new-only'])):
        properties = schema['properties']['claims']['items']['properties']
        if repair:
            assert set(properties) == {'text', 'source_claim_number', 'support_ids'}
            # 보완 대상이 없으면 후보가 있어도 선택 권한이 없다.
            assert schema['properties']['claims']['maxItems'] == 0
        else:
            assert properties['evidence_ids']['items']['enum'] == expected
            assert properties['supports']['items']['properties']['evidence_id']['enum'] == expected
    # 호출 간 후보 유출이나 공용 스키마 변경이 없어야 한다.
    base = _SCHEMA['properties']['claims']['items']['properties']
    assert 'enum' not in base['evidence_ids']['items']
    assert 'enum' not in base['supports']['items']['properties']['evidence_id']
    assert post.call_count == 2


def test_repair_request_preserves_schema_timeout_and_private_fields(setup):
    from solo_leveling.application.evidence_qa.ports import ContinuationRepairTarget
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim
    generator, evidence, post, _ = setup
    draft = GeneratedAnswerDraft((GeneratedClaim('미완결 주장', ('ev-1',)),))
    evidence += (EvidenceInput('ev-2', 'private-next-chunk', '이어지는 번역',
                               'continued sentence.', None, 7, follows_evidence_id='ev-1'),)
    result = generator.repair_claims('조건은?', evidence, draft,
                                    (ContinuationRepairTarget(1, 'ev-1', 'ev-2'),))
    assert not result.claims
    post.assert_called_once()
    payload = post.call_args.kwargs['json']
    data = json.loads(payload['input'][0]['content'])
    assert data['draft']['claims'][0]['text'] == '미완결 주장'
    assert data['repair_targets'] == [dict(claim_number=1, evidence_id='ev-1',
        next_evidence_id='ev-2', reason_code='UNFINISHED_TAIL')]
    assert 'ID만 추가해서는 안 된다' in payload['instructions']
    assert payload['text']['format']['strict'] is True
    assert post.call_args.kwargs['timeout'] == (5.0, 30.0)
    assert 'internal-chunk' not in json.dumps(payload)
    context, = data['repair_contexts']
    assert context['draft_claim'] == data['draft']['claims'][0]
    assert context['left']['evidence_id'] == 'ev-1'
    assert context['next']['evidence_id'] == 'ev-2'
    assert 'private-next-chunk' not in json.dumps(payload)


def test_repair_timeout_has_no_transport_retry(setup):
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft
    generator, evidence, post, _ = setup
    post.side_effect = requests.Timeout('비공개')
    with pytest.raises(OpenAIGenerationError) as caught:
        generator.repair_claims('질문', evidence, GeneratedAnswerDraft(()), ())
    assert caught.value.kind == FailureKind.TIMEOUT
    post.assert_called_once()


@pytest.mark.parametrize('reason', ['UNRESOLVED_PREFIX', 'SUPPORT_NOT_FOUND'])
def test_repair_instructions_for_quote_quality(setup, reason):
    from solo_leveling.application.evidence_qa.ports import ContinuationRepairTarget
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim
    generator, evidence, post, _ = setup
    generator.repair_claims('질문', evidence, GeneratedAnswerDraft((GeneratedClaim('주장', ('ev-1',)),)),
        (ContinuationRepairTarget(1, 'ev-1', reason_code=reason),))
    payload = post.call_args.kwargs['json']
    target = json.loads(payload['input'][0]['content'])['repair_targets'][0]
    assert target['reason_code'] == reason
    assert target['next_evidence_id'] is None
    assert '필요한 조건절을 제거하지 마라' in payload['instructions']
    assert 'support_ids에는 그 대상에 허용된 selection_id만 선택하라' in payload['instructions']
    assert 'quote_original로 복사하라' not in payload['instructions']
    assert json.loads(payload['input'][0]['content'])['repair_contexts'][0]['next'] is None


@pytest.mark.parametrize('case', ['missing_left', 'missing_next', 'wrong_link', 'wrong_number'])
def test_invalid_repair_pair_is_not_sent(setup, case):
    from solo_leveling.application.evidence_qa.ports import ContinuationRepairTarget
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim
    generator, evidence, post, _ = setup
    if case == 'wrong_link':
        evidence += (EvidenceInput('ev-2', 'c2', '번역', 'Original.', None, 7),)
    target = ContinuationRepairTarget(2 if case == 'wrong_number' else 1,
        'absent' if case == 'missing_left' else 'ev-1',
        'ev-2' if case in ('missing_next', 'wrong_link') else None)
    with pytest.raises(ValueError):
        generator.repair_claims('질문', evidence,
            GeneratedAnswerDraft((GeneratedClaim('주장', ('ev-1',)),)), (target,))
    post.assert_not_called()


@pytest.mark.parametrize('count', [0, 1, 2])
def test_repair_claim_limit_counts_unique_targets_and_does_not_leak(setup, count):
    from solo_leveling.application.evidence_qa.ports import ContinuationRepairTarget
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim
    from solo_leveling.infrastructure.generation.openai_generator import _SCHEMA
    generator, evidence, post, _ = setup
    draft = GeneratedAnswerDraft(tuple(GeneratedClaim('주장', ('ev-1',)) for _ in range(count)))
    targets = tuple(ContinuationRepairTarget(i + 1, 'ev-1', reason_code='SUPPORT_NOT_FOUND')
                    for i in range(count))
    generator.repair_claims('질문', evidence, draft, targets + targets)
    schema = post.call_args.kwargs['json']['text']['format']['schema']
    item = schema['properties']['claims']['items']
    branches = item.get('anyOf', [item])
    assert [n for branch in branches for n in branch['properties']['source_claim_number']['enum']] == (list(range(1, count + 1)) or [1])
    assert all('source_claim_number' in branch['required'] for branch in branches)
    assert schema['properties']['claims'].get('maxItems') == (0 if count == 0 else None)
    assert schema['properties']['claims'].get('minItems', 0) == 0
    generator.generate_claims('질문', evidence)
    assert 'maxItems' not in post.call_args.kwargs['json']['text']['format']['schema']['properties']['claims']
    assert 'maxItems' not in _SCHEMA['properties']['claims']


def test_repair_payload_cannot_leak_excluded_sentences_through_other_fields(setup):
    from solo_leveling.application.evidence_qa.ports import ContinuationRepairTarget
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim, ClaimSupport
    generator, _, post, _ = setup
    left = EvidenceInput('left', 'c1', '제외할 번역 앞', 'Prior explanation. Faster when the sequence\n6',
                         None, 6, False, True)
    right = EvidenceInput('right', 'c2', '제외할 번역 뒤', 'length n is smaller than d. Restrict to r neighbors.',
                          None, 7, True, False, 'left')
    generator.repair_claims('조건과 경로 길이는?', (left, right),
        GeneratedAnswerDraft((GeneratedClaim('계산 복잡도가 더 빠르다.', ('left',),
            (ClaimSupport('left', left.original_text),)),)), (ContinuationRepairTarget(1, 'left', 'right'),))
    data = json.loads(post.call_args.kwargs['json']['input'][0]['content'])
    serialized = json.dumps(data, ensure_ascii=False)
    for excluded in ('Prior explanation', 'Restrict to r neighbors', '제외할 번역', '조건과 경로 길이는?'):
        assert excluded not in serialized
    option, = data['repair_quote_options']
    assert [q['pdf_page'] for q in option['quotes']] == [6, 7]
    assert option['quotes'][1]['text'] == 'length n is smaller than d.'


def test_repair_does_not_send_other_question_or_unrelated_evidence(setup):
    from solo_leveling.application.evidence_qa.ports import ContinuationRepairTarget
    from solo_leveling.domain.evidence_qa import GeneratedAnswerDraft, GeneratedClaim
    generator, evidence, post, _ = setup
    unrelated = EvidenceInput('path-only', 'private-path', '경로 길이', 'Short paths help learning.', None, 6)
    generator.repair_claims('계산 조건과 경로 길이는?', (*evidence, unrelated),
        GeneratedAnswerDraft((GeneratedClaim('계산 조건', ('ev-1',)),)),
        (ContinuationRepairTarget(1, 'ev-1', reason_code='SUPPORT_NOT_FOUND'),))
    data = json.loads(post.call_args.kwargs['json']['input'][0]['content'])
    assert '계산 조건과 경로 길이는?' not in json.dumps(data, ensure_ascii=False)
    assert 'path-only' not in json.dumps(data)
    assert 'Short paths' not in json.dumps(data)
    assert data['draft']['claims'][0]['text'] == '계산 조건'


@pytest.mark.parametrize('allow,local', [(False, False), (True, True), (False, True)])
def test_external_call_requires_permission(setup, allow, local):
    _, evidence, post, _ = setup
    generator = OpenAIClaimGenerator(OpenAIGenerationSettings('key', allow_external_api=allow, local_only=local))
    with pytest.raises(GenerationUnavailable):
        generator.generate_claims('질문', evidence)
    post.assert_not_called()


@pytest.mark.parametrize('status,kind', [(429, FailureKind.RATE_LIMIT),
    (401, FailureKind.AUTHENTICATION), (403, FailureKind.AUTHENTICATION),
    (500, FailureKind.PROVIDER), (503, FailureKind.PROVIDER),
    (400, FailureKind.REQUEST), (302, FailureKind.REQUEST)])
def test_http_errors_are_sanitized_and_not_retried(setup, status, kind):
    generator, evidence, post, response = setup
    response.status_code = status
    response.text = '비공개 응답'
    with pytest.raises(OpenAIGenerationError) as caught:
        generator.generate_claims('질문', evidence)
    assert caught.value.kind == kind
    assert '비공개' not in str(caught.value)
    post.assert_called_once()
    response.close.assert_called_once()


@pytest.mark.parametrize('error,kind', [(requests.Timeout('secret'), FailureKind.TIMEOUT),
    (requests.ConnectionError('secret'), FailureKind.CONNECTION)])
def test_transport_error(setup, error, kind):
    generator, evidence, post, _ = setup
    post.side_effect = error
    with pytest.raises(OpenAIGenerationError) as caught:
        generator.generate_claims('질문', evidence)
    assert caught.value.kind == kind
    assert 'secret' not in str(caught.value)
    post.assert_called_once()


@pytest.mark.parametrize('status', ['incomplete', 'failed', 'cancelled', 'queued', 'in_progress'])
def test_unfinished_response_never_uses_partial_text(setup, status):
    generator, evidence, _, response = setup
    data = envelope()
    data['status'] = status
    response.json.return_value = data
    with pytest.raises(OpenAIGenerationError):
        generator.generate_claims('질문', evidence)


def test_refusal_is_not_empty_evidence(setup):
    generator, evidence, _, response = setup
    data = envelope()
    data['output'][0]['content'] = [{'type': 'refusal', 'refusal': '비공개 사유'}]
    response.json.return_value = data
    with pytest.raises(OpenAIGenerationError) as caught:
        generator.generate_claims('질문', evidence)
    assert caught.value.kind == FailureKind.REFUSAL


@pytest.mark.parametrize('data', [None, {}, {'status': 'completed', 'output': []},
    {'status': 'completed', 'output': [None]}, envelope('not json'),
    {'status': 'completed', 'output': [{'type': 'function_call'}]}])
def test_invalid_envelope_or_body(setup, data):
    generator, evidence, _, response = setup
    response.json.return_value = data
    with pytest.raises(GenerationFormatError):
        generator.generate_claims('질문', evidence)
    response.close.assert_called_once()


def test_non_json_http_response(setup):
    generator, evidence, _, response = setup
    response.json.side_effect = ValueError('비공개 본문')
    with pytest.raises(GenerationFormatError):
        generator.generate_claims('질문', evidence)
    response.close.assert_called_once()


def test_empty_evidence_skips_http(setup):
    generator, _, post, _ = setup
    assert generator.generate_claims('질문', ()).claims == ()
    post.assert_not_called()


def test_reasoning_item_is_not_treated_as_answer(setup):
    generator, evidence, _, response = setup
    data = envelope()
    data['output'].insert(0, {'type': 'reasoning', 'summary': []})
    response.json.return_value = data
    assert generator.generate_claims('질문', evidence).claims == ()


@pytest.mark.parametrize('change', ['multiple', 'wrong_role', 'unfinished_message', 'bad_content'])
def test_ambiguous_messages_are_rejected(setup, change):
    generator, evidence, _, response = setup
    data = envelope()
    message = data['output'][0]
    if change == 'multiple':
        data['output'].append(message.copy())
    elif change == 'wrong_role':
        message['role'] = 'user'
    elif change == 'unfinished_message':
        message['status'] = 'in_progress'
    else:
        message['content'] = [None]
    response.json.return_value = data
    with pytest.raises(GenerationFormatError):
        generator.generate_claims('질문', evidence)


def test_invalid_inputs_do_not_send_request(setup):
    generator, evidence, post, _ = setup
    with pytest.raises(ValueError):
        generator.generate_claims(' ', evidence)
    with pytest.raises(ValueError):
        generator.generate_claims('질문', evidence + evidence)
    post.assert_not_called()


def test_env_defaults_and_overrides():
    settings = OpenAIGenerationSettings.from_env({'OPENAI_API_KEY': 'key'})
    assert settings.model == 'gpt-5.4-mini'
    assert not settings.allow_external_api
    settings = OpenAIGenerationSettings.from_env({'OPENAI_API_KEY': 'key',
        'GENERATION_MODEL': 'configured-model', 'ALLOW_EXTERNAL_API': 'true',
        'GENERATION_TIMEOUT_SECONDS': '15', 'GENERATION_MAX_OUTPUT_TOKENS': '1000'})
    assert settings.model == 'configured-model'
    assert settings.timeout_seconds == 15 and settings.max_output_tokens == 1000
    assert settings.allow_external_api


@pytest.mark.parametrize('extra', [{'OPENAI_API_KEY': ''}, {'GENERATION_PROVIDER': 'other'},
    {'GENERATION_MODEL': ''}, {'ALLOW_EXTERNAL_API': 'yes'}, {'LOCAL_ONLY': ''},
    {'GENERATION_TIMEOUT_SECONDS': 'nan'}, {'GENERATION_TIMEOUT_SECONDS': '0'},
    {'GENERATION_MAX_OUTPUT_TOKENS': '0'}, {'GENERATION_MAX_OUTPUT_TOKENS': 'x'}])
def test_invalid_settings(extra):
    with pytest.raises(ValueError):
        OpenAIGenerationSettings.from_env({'OPENAI_API_KEY': 'key', **extra})
