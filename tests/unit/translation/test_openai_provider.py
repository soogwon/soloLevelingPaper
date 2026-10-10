"""실제 통신 없이 번역 요청·실패·기존 배치 계약을 확인한다."""

from dataclasses import replace
import json
from unittest.mock import Mock

import pytest
import requests

from solo_leveling.application.translation.ports import ProviderFailure, TranslationProviderUnavailable
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.models import Chunk
from solo_leveling.domain.translation import TranslationRequest, TranslationSettings
from solo_leveling.infrastructure.translation.openai_provider import (
    OpenAITranslationConfig, OpenAITranslationProvider, PROMPT_VERSION,
)


def envelope(text='{"text": "정확도는 82%였다."}'):
    return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
        'status': 'completed', 'content': [{'type': 'output_text', 'text': text}]}]}


@pytest.fixture
def setup(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = envelope()
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, 'post', post)
    config = OpenAITranslationConfig('test-secret', allow_external_api=True)
    request = TranslationRequest('private-chunk', 'private-parse', 'Accuracy was 82%.')
    return OpenAITranslationProvider(config), request, config.translation_settings, post, response


def test_translation_and_request_contract(setup):
    provider, request, settings, post, response = setup
    assert provider.translate(request, settings) == '정확도는 82%였다.'
    args, kwargs = post.call_args
    assert args == ('https://api.openai.com/v1/responses',)
    assert kwargs['timeout'] == (5.0, 30.0) and not kwargs['allow_redirects']
    payload = kwargs['json']
    assert payload['model'] == settings.model
    assert payload['store'] is False and payload['max_output_tokens'] == 4096
    assert payload['text']['format']['strict'] is True
    assert '요약·해설' in payload['instructions']
    assert '수치' in payload['instructions'] and '번역할 자료' in payload['instructions']
    assert json.loads(payload['input'][0]['content']) == {
        'original_text': request.original_text, 'target_language': 'ko'}
    assert 'private-chunk' not in json.dumps(payload)
    assert 'private-parse' not in json.dumps(payload)
    assert 'test-secret' not in repr(provider.config)
    response.close.assert_called_once()


@pytest.mark.parametrize('field,value', [('model', 'other'), ('prompt_version', 'other'), ('provider', 'fake')])
def test_revision_settings_must_match_before_call(setup, field, value):
    provider, request, settings, post, _ = setup
    with pytest.raises(ValueError):
        provider.translate(request, replace(settings, **{field: value}))
    post.assert_not_called()


@pytest.mark.parametrize('allow,local', [(False, False), (True, True)])
def test_external_call_guard(setup, allow, local):
    _, request, settings, post, _ = setup
    provider = OpenAITranslationProvider(OpenAITranslationConfig('key', allow_external_api=allow, local_only=local))
    with pytest.raises(TranslationProviderUnavailable):
        provider.translate(request, settings)
    post.assert_not_called()


@pytest.mark.parametrize('status', [400, 401, 403, 429, 500, 503, 302])
def test_http_failures_are_sanitized_without_retry(setup, status):
    provider, request, settings, post, response = setup
    response.status_code = status
    response.text = 'test-secret'
    with pytest.raises(TranslationProviderUnavailable) as caught:
        provider.translate(request, settings)
    assert 'test-secret' not in str(caught.value)
    post.assert_called_once()
    response.close.assert_called_once()


@pytest.mark.parametrize('error', [requests.Timeout('secret'), requests.ConnectionError('secret')])
def test_transport_failure(setup, error):
    provider, request, settings, post, _ = setup
    post.side_effect = error
    with pytest.raises(TranslationProviderUnavailable) as caught:
        provider.translate(request, settings)
    assert 'secret' not in str(caught.value)
    assert caught.value.code == (ProviderFailure.TIMEOUT if isinstance(error, requests.Timeout)
                                 else ProviderFailure.CONNECTION)
    post.assert_called_once()


def test_tls_error_is_not_retried(setup):
    provider, request, settings, post, _ = setup
    post.side_effect = requests.exceptions.SSLError('secret')
    with pytest.raises(TranslationProviderUnavailable) as caught:
        provider.translate(request, settings)
    assert not caught.value.retryable and 'secret' not in str(caught.value)


@pytest.mark.parametrize('data,code', [
    ({'status': 'incomplete'}, ProviderFailure.INCOMPLETE),
    ({'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
      'status': 'completed', 'content': [{'type': 'refusal', 'refusal': 'secret'}]}]}, ProviderFailure.REFUSAL),
    (envelope('{'), ProviderFailure.INVALID_RESPONSE),
])
def test_response_failure_classification(setup, data, code):
    provider, request, settings, _, response = setup
    response.json.return_value = data
    with pytest.raises(TranslationProviderUnavailable) as caught:
        provider.translate(request, settings)
    assert caught.value.code == code and not caught.value.retryable
    response.close.assert_called_once()


@pytest.mark.parametrize('data', [None, {}, {'status': 'incomplete', 'output': []},
    {'status': 'failed', 'output': []}, {'status': 'completed', 'output': []},
    {'status': 'completed', 'output': [None]},
    {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
        'content': [{'type': 'refusal', 'refusal': 'secret'}]}]},
    envelope('{'), envelope('[]'), envelope('{"text": null}'), envelope('{"text": 1}'),
    envelope('{"text": "a", "text": "b"}'), envelope('{"text": "a", "extra": 1}'),
    envelope('{"text": NaN}'), envelope('```json\n{"text":"a"}\n```')])
def test_invalid_output_is_provider_failure(setup, data):
    provider, request, settings, _, response = setup
    response.json.return_value = data
    with pytest.raises(TranslationProviderUnavailable):
        provider.translate(request, settings)
    response.close.assert_called_once()


def test_non_json_response(setup):
    provider, request, settings, _, response = setup
    response.json.side_effect = ValueError('secret')
    with pytest.raises(TranslationProviderUnavailable):
        provider.translate(request, settings)
    response.close.assert_called_once()


def test_existing_service_handles_success_empty_and_failure(setup):
    provider, _, settings, post, _ = setup
    responses = [Mock(status_code=200) for _ in range(3)]
    for response, data in zip(responses, [envelope(), envelope('{"text":" "}'), {'status': 'incomplete'}]):
        response.json.return_value = data
    post.side_effect = responses
    chunks = [Chunk(str(i), 'p', i, 'Accuracy was 82%.') for i in range(3)]
    batch = TranslationService(provider).translate(chunks, settings)
    assert batch.success_count == 1 and batch.failure_count == 2
    assert batch.items[1].failure_code.value == 'empty_translation'
    assert batch.items[2].failure_code.value == 'provider_unavailable'
    assert batch.items[2].text is None
    assert all(chunk.text is None for chunk in chunks)
    assert batch.revision.model == settings.model


def test_formula_may_be_unchanged(setup):
    provider, _, settings, _, response = setup
    response.json.return_value = envelope('{"text":"x = y"}')
    result = TranslationService(provider).translate([Chunk('c', 'p', 0, 'x = y')], settings)
    assert result.success_count == 1 and result.items[0].text == 'x = y'


def test_environment_is_independent_from_generation():
    config = OpenAITranslationConfig.from_env({'OPENAI_API_KEY': 'key',
        'GENERATION_MODEL': 'unrelated', 'GENERATION_MAX_OUTPUT_TOKENS': '1'})
    assert config.translation_settings.model == 'gpt-5.4-mini'
    assert config.translation_settings.prompt_version == PROMPT_VERSION
    assert config.max_output_tokens == 4096 and not config.allow_external_api
    changed = OpenAITranslationConfig.from_env({'OPENAI_API_KEY': 'key',
        'TRANSLATION_MODEL': 'configured-model', 'ALLOW_EXTERNAL_API': 'true'})
    assert changed.translation_settings.model == 'configured-model'
    assert changed.allow_external_api


@pytest.mark.parametrize('extra', [{'OPENAI_API_KEY': ''}, {'TRANSLATION_PROVIDER': 'fake'},
    {'TRANSLATION_PROMPT_VERSION': 'invented'}, {'TRANSLATION_MODEL': ''},
    {'TRANSLATION_TARGET_LANGUAGE': 'en'}, {'TRANSLATION_TIMEOUT_SECONDS': 'nan'},
    {'TRANSLATION_MAX_OUTPUT_TOKENS': '0'}, {'TRANSLATION_TIMEOUT_SECONDS': 'x'},
    {'ALLOW_EXTERNAL_API': 'yes'}, {'LOCAL_ONLY': ''}])
def test_invalid_configuration(extra):
    with pytest.raises(ValueError):
        OpenAITranslationConfig.from_env({'OPENAI_API_KEY': 'key', **extra})
