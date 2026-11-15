"""실제 네트워크 없이 기존 adapter의 전송 경계와 unknown 후 재시도 차단을 검증한다."""
import json
from unittest.mock import Mock

import pytest
import requests

from solo_leveling.application.provider_dispatch import (
    AttemptPermit, DispatchError, DispatchIntent, ProviderDispatch,
)
from solo_leveling.domain.evidence_qa import EvidenceInput, GeneratedAnswerDraft
from solo_leveling.domain.models import Chunk
from solo_leveling.infrastructure.generation.gated_responses import (
    GatedResponsesTransport, PreparedProviderAttempt,
)
from solo_leveling.infrastructure.generation.openai_generator import OpenAIClaimGenerator, OpenAIGenerationSettings
from solo_leveling.infrastructure.generation.semantic_verifier import OpenAISemanticVerifier, SemanticSettings
from solo_leveling.infrastructure.translation.openai_provider import OpenAITranslationConfig, OpenAITranslationProvider
from solo_leveling.application.translation.service import TranslationService


class Ledger:
    def __init__(self):
        self.requests, self.observed, self.unknowns = [], [], []
        self.allowed = True

    def authorize(self, request):
        self.requests.append(request)
        return AttemptPermit(str(len(self.requests)), request) if self.allowed else None

    def observe(self, *args): self.observed.append(args)
    def unknown(self, key): self.unknowns.append(key)


def envelope(text):
    return {'status': 'completed', 'usage': {'input_tokens': 5, 'output_tokens': 2},
        'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': json.dumps(text)}]}]}


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(requests, 'post', Mock(side_effect=AssertionError('ungated network forbidden')))
    ledger = Ledger()
    gate = ProviderDispatch(ledger, DispatchIntent('owner', 'execution', 'lease', 'hash', 'storage', 'ai', 'notice'))
    response = Mock(status_code=200)
    response.json.return_value = envelope({'claims': []})
    post = Mock(return_value=response)
    def prepare(stage, payload, digest):
        return PreparedProviderAttempt(f'{stage}-{len(ledger.requests)}', digest, 100)
    transport = GatedResponsesTransport(gate, prepare, post=post)
    return ledger, transport, post, response


def test_generation_and_repair_each_use_gate_and_existing_parser(setup):
    ledger, transport, post, response = setup
    generator = OpenAIClaimGenerator(OpenAIGenerationSettings('secret', allow_external_api=True), transport=transport)
    evidence = (EvidenceInput('e1', 'c1', '번역', 'Original.', None, 6),)
    assert generator.generate_claims('질문', evidence).claims == ()
    assert generator.repair_claims('질문', evidence, GeneratedAnswerDraft(()), ()).claims == ()
    assert [r.stage for r in ledger.requests] == ['generation', 'repair']
    assert len(ledger.observed) == 2 and post.call_count == response.close.call_count == 2
    assert post.call_args.kwargs['allow_redirects'] is False


def test_semantic_adapter_uses_same_gate(setup):
    ledger, transport, _, response = setup
    verifier = OpenAISemanticVerifier(OpenAIGenerationSettings('secret', allow_external_api=True),
        SemanticSettings(True), transport=transport)
    from solo_leveling.domain.evidence_qa import Claim
    claims = (Claim('cl1', '주장', ('e1',)),)
    evidence = (EvidenceInput('e1', 'c1', '번역', 'Original.', None, 6),)
    from solo_leveling.application.evidence_qa.original_quotes import original_quotes
    quote_id = original_quotes(claims, evidence)[0]['quote_id']
    checks = {k: {'status': 'not_applicable', 'quote_ids': []}
              for k in ('content', 'direction', 'conditions', 'causality', 'metrics')}
    checks['content'] = {'status': 'supported', 'quote_ids': [quote_id]}
    response.json.return_value = envelope({'judgments': [
        {'claim_id': 'cl1', 'checks': checks, 'verdict': 'supported', 'reason': 'full_support'}]})
    assert verifier.verify(claims, evidence)[0].claim_id == 'cl1'
    assert ledger.requests[0].stage == 'semantic_verification'
    assert len(ledger.observed) == 1


def test_translation_unknown_stops_service_retry_and_next_chunk(setup):
    ledger, transport, post, response = setup
    response.json.return_value = {'private': 'body-without-usage'}
    config = OpenAITranslationConfig('secret', allow_external_api=True)
    provider = OpenAITranslationProvider(config, transport=transport)
    with pytest.raises(DispatchError, match='^DISPATCH_USAGE_UNKNOWN$'):
        TranslationService(provider).translate(
            (Chunk('c1', 'parse', 0, 'One.'), Chunk('c2', 'parse', 1, 'Two.')), config.translation_settings)
    assert post.call_count == 1 and ledger.unknowns == ['1'] and ledger.observed == []


def test_translation_success_uses_existing_result_parser(setup):
    ledger, transport, post, response = setup
    response.json.return_value = envelope({'text': '정확도는 82%였다.'})
    from solo_leveling.domain.translation import TranslationRequest
    config = OpenAITranslationConfig('secret', allow_external_api=True)
    provider = OpenAITranslationProvider(config, transport=transport)
    assert provider.translate(TranslationRequest('c1', 'p', 'Accuracy was 82%.'),
                              config.translation_settings) == '정확도는 82%였다.'
    assert ledger.requests[0].stage == 'translation' and post.call_count == 1


@pytest.mark.parametrize('mode', ['denied', 'bad-plan', 'no-bound'])
def test_preflight_failures_do_not_send(setup, mode):
    ledger, transport, post, _ = setup
    ledger.allowed = mode != 'denied'
    if mode != 'denied':
        transport.prepare = lambda stage, p, d: PreparedProviderAttempt('key', 'bad' if mode == 'bad-plan' else d,
                                                                       None if mode == 'no-bound' else 100)
    with pytest.raises(DispatchError):
        transport.send(OpenAIGenerationSettings('secret', allow_external_api=True),
            {'model': 'gpt-5.4-mini', 'max_output_tokens': 2048, 'store': False}, 'generation')
    post.assert_not_called()


@pytest.mark.parametrize('mode', ['timeout', 'http-error', 'json-error', 'bad-usage'])
def test_uncertain_attempt_blocks_next_send_and_closes_response(setup, mode):
    ledger, transport, post, response = setup
    if mode == 'timeout': post.side_effect = requests.Timeout('private')
    if mode == 'http-error': response.status_code = 429
    if mode == 'json-error': response.json.side_effect = ValueError('private')
    if mode == 'bad-usage': response.json.return_value['usage']['input_tokens'] = True
    settings = OpenAIGenerationSettings('secret', allow_external_api=True)
    payload = {'model': settings.model, 'store': False, 'max_output_tokens': 2048}
    for expected in ['DISPATCH_USAGE_UNKNOWN', 'DISPATCH_BLOCKED']:
        with pytest.raises(DispatchError, match='^'+expected+'$'):
            transport.send(settings, payload, 'generation')
    assert post.call_count == 1 and ledger.unknowns == ['1']
    assert response.close.call_count == (0 if mode == 'timeout' else 1)


def test_preparer_mutation_cannot_change_frozen_transmission(setup):
    ledger, transport, post, _ = setup
    def mutate(stage, payload, digest):
        payload['input'] = 'different-private-content'
        return PreparedProviderAttempt('stable-key', digest, 100)
    transport.prepare = mutate
    settings = OpenAIGenerationSettings('secret', allow_external_api=True)
    payload = {'model': settings.model, 'store': False, 'max_output_tokens': 2048, 'input': 'original'}
    transport.send(settings, payload, 'generation')
    assert post.call_args.kwargs['json']['input'] == payload['input'] == 'original'
    assert ledger.observed == [('1', 5, 2)]


def test_disabled_external_guard_precedes_preparation(setup):
    _, transport, post, _ = setup
    transport.prepare = Mock(side_effect=AssertionError('must not prepare'))
    with pytest.raises(DispatchError, match='DISPATCH_DISABLED'):
        transport.send(OpenAIGenerationSettings('secret'), {}, 'generation')
    post.assert_not_called()
