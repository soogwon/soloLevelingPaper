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
        {'text': '주장', 'evidence_ids': ['ev-1']},
    ]}))
    result = generator.generate_claims('질문', evidence)
    assert result.claims[0].evidence_ids == ('ev-1',)
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
