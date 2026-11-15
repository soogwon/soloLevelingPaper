"""DB가 없는 로컬에서도 실제 생성/의미 parser와 합성 HTTP 본문을 함께 확인한다."""
# ruff: noqa: F811
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import requests

from tests.gated_answer_fixture import synthetic_answer_response
from tests.unit.test_gated_responses import Ledger
from tests.unit.test_remote_answer_preparation import parts  # noqa: F401
from solo_leveling.application.provider_dispatch import DispatchIntent, ProviderDispatch
from solo_leveling.application.evidence_qa.semantic import SemanticVerificationService
from solo_leveling.domain.evidence_qa import EvidenceInput, Claim
from solo_leveling.infrastructure.generation.gated_responses import GatedResponsesTransport, PreparedProviderAttempt
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings, OpenAIClaimGenerator
from solo_leveling.infrastructure.generation.semantic_verifier import OpenAISemanticVerifier, SemanticSettings


def gated(monkeypatch):
    monkeypatch.setattr(requests, 'post', Mock(side_effect=AssertionError('real network forbidden')))
    ledger = Ledger()
    gate = ProviderDispatch(ledger, DispatchIntent('alice', 'exec', 'lease', 'hash', 'storage', 'ai', 'notice'))
    def reply(url, **kw):
        return NS(status_code=200, json=lambda: synthetic_answer_response(kw['json']), close=Mock())
    post = Mock(side_effect=reply)
    transport = GatedResponsesTransport(gate,
        lambda stage, p, d: PreparedProviderAttempt(stage, d, 100), post=post)
    settings = OpenAIGenerationSettings('synthetic-not-a-key', allow_external_api=True)
    return ledger, transport, settings, post


def test_synthetic_envelopes_pass_real_claim_and_semantic_parsers_without_network(monkeypatch):
    ledger, transport, settings, _ = gated(monkeypatch)
    evidence = (EvidenceInput('e1', 'c1', '합성 원문이다.', 'Synthetic original.', None, 6),)
    draft = OpenAIClaimGenerator(settings, transport=transport).generate_claims('합성 설명은?', evidence)
    assert len(draft.claims) == 1 and draft.claims[0].text == evidence[0].text_ko
    claims = (Claim('c', draft.claims[0].text, ('e1',)),)
    report = SemanticVerificationService(OpenAISemanticVerifier(settings, SemanticSettings(True),
        transport=transport)).check(claims, evidence, question='합성 설명은?')
    assert report.failure is None and report.level.value == 'model_checked'
    assert report.judgments[0].verdict.value == 'supported'
    assert [r.stage for r in ledger.requests] == ['generation', 'semantic_verification']
    assert ledger.observed == [('1', 8, 4), ('2', 8, 4)]


def test_full_preparation_and_commit_boundary_accepts_same_gated_synthetic_responses(monkeypatch, parts):
    from solo_leveling.application.evidence_qa.remote_answer import RemoteAnswerPreparationService
    from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters
    from solo_leveling.application.evidence_qa.remote_input import question_input_hash
    from solo_leveling.infrastructure.retrieval.remote_answer_factory import PersistedRemoteAnswerService
    from tests.unit.test_persisted_remote_answer import CONFIG
    binding, _, reader, _, _, search = parts
    ledger, transport, settings, post = gated(monkeypatch)
    filters = RemoteSearchFilters((6,), ('s4',))
    intent = replace(transport.dispatch.intent, input_hash=question_input_hash(binding, '합성 설명은?', filters, 2, CONFIG))
    transport.dispatch.intent = intent
    prep = RemoteAnswerPreparationService(search, reader, OpenAIClaimGenerator(settings, transport=transport),
        semantic_verification=SemanticVerificationService(OpenAISemanticVerifier(settings, SemanticSettings(True),
                                                                                 transport=transport)))
    writer = NS(save=Mock(return_value={'committed': True}))
    service = PersistedRemoteAnswerService(lambda _: binding, prep, writer, lambda _: True, CONFIG)
    payload = service.execute(intent, '합성 설명은?', binding.context_id, filters=filters, top_k=2)
    assert payload == {'committed': True}
    writer.save.assert_called_once()
    prepared = writer.save.call_args.args[1]
    assert prepared.response.result.status.value == 'ok'
    assert prepared.response.verification_level.value == 'model_checked'
    assert post.call_count == 2 and len(ledger.observed) == 2
