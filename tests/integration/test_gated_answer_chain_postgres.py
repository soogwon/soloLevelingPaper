"""B 실제 저장+원장+Responses parser의 통합 회귀. HTTP/인증/임베딩은 합성 대역이다."""
# ruff: noqa: F811
from dataclasses import replace
import os
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import requests

from tests.integration.test_remote_processing_postgres import pool, db, stores, new_id  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from tests.unit.test_persisted_remote_answer import CONFIG
from tests.gated_answer_fixture import synthetic_answer_response
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters
from solo_leveling.application.evidence_qa.remote_input import question_input_hash
from solo_leveling.application.evidence_qa.remote_search import QueryEmbedding
from solo_leveling.application.evidence_qa.semantic import SemanticVerificationService
from solo_leveling.application.provider_dispatch import DispatchError, DispatchBudgetExceeded
from solo_leveling.application.remote_replay import ExecutionLookup, read_existing_execution
from solo_leveling.infrastructure.generation.execution_budget import ExecutionBudget
from solo_leveling.infrastructure.generation.gated_responses import PreparedProviderAttempt
from solo_leveling.infrastructure.generation.persisted_dispatch import create_persisted_responses_transport
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings, OpenAIClaimGenerator
from solo_leveling.infrastructure.generation.semantic_verifier import OpenAISemanticVerifier, SemanticSettings
from solo_leveling.infrastructure.retrieval.remote_answer_factory import create_persisted_remote_answer_service
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),
                                reason='실제 PostgreSQL+pgvector 테스트 DSN 필요')
QUESTION = '합성 설명을 알려줘 CANARY_GATED_397_QUESTION'


@pytest.fixture(autouse=True)
def forbid_real_provider_network(monkeypatch):
    monkeypatch.setattr(requests, 'post', Mock(side_effect=AssertionError('real provider network forbidden')))


def chain(db, stores, pool, filters, *, max_calls=2):
    intent, prepared = setup_answer(db, stores, pool)
    binding = prepared.binding
    config = dict(CONFIG, semantic_prompt_version=OpenAISemanticVerifier.prompt_version)
    digest = question_input_hash(binding, QUESTION, filters, 1, config)
    # 테스트에만 쓰는 승인 대상도 실제 canonical 입력과 같게 맞춘다.
    db('UPDATE server_remote_executions SET input_hash=%s WHERE execution_id=%s', (digest, intent.execution_id))
    db('UPDATE server_remote_consents SET input_hash=%s WHERE consent_id=ANY(%s::text[])',
       (digest, [intent.storage_consent_id, intent.ai_consent_id]))
    intent = replace(intent, input_hash=digest)
    policy = new_id()
    db("""INSERT INTO server_dispatch_controls(control_id,generation,dispatch_enabled)
          VALUES ('provider_dispatch',1,true) ON CONFLICT(control_id) DO UPDATE SET dispatch_enabled=true""")
    for kind, owner in [('service', 'service'), ('owner', 'alice')]:
        db('''INSERT INTO server_dispatch_budget_limits
            (scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
            VALUES (%s,%s,%s,'fixture-window',10000,10000)''', (kind, owner, policy))

    def reply(url, **kw):
        return NS(status_code=200, json=lambda: synthetic_answer_response(kw['json']), close=Mock())
    post = Mock(side_effect=reply)
    embed = Mock(side_effect=lambda q, b: QueryEmbedding((1., 0.), b.model_fingerprint))
    settings = OpenAIGenerationSettings('synthetic-not-a-key', model=config['generation_model'],
                                        max_output_tokens=256, allow_external_api=True)
    resolver = PostgresRemoteContextResolver(pool)

    def build(prefix='first'):
        # 결정적인 fixture key/상한이다. 제품용 영속 key나 실제 모델 토큰 추정기가 아니다.
        transport = create_persisted_responses_transport(pool, intent, policy, 'fixture-window',
            lambda stage, p, d: PreparedProviderAttempt(f'{prefix}:{stage}', d, 100), post=post,
            execution_budget=ExecutionBudget(max_calls, 1000, 1000, 600))
        return create_persisted_remote_answer_service(pool, lambda ctx: resolver.resolve('alice', ctx),
            NS(embed=embed), OpenAIClaimGenerator(settings, transport=transport), config,
            semantic_verification=SemanticVerificationService(OpenAISemanticVerifier(settings,
                SemanticSettings(True, model=config['semantic_model'], max_output_tokens=256), transport=transport)))

    lookup = ExecutionLookup('alice', 'ask', intent.execution_id, intent.input_hash, binding.context_id)
    return NS(intent=intent, binding=binding, post=post, embed=embed, reply=reply, build=build, lookup=lookup)


def attempts(db, value):
    return db('''SELECT stage,status,actual_input_tokens,actual_output_tokens FROM server_provider_attempts
                 WHERE execution_id=%s ORDER BY stage''', (value.intent.execution_id,))


def assert_no_answer_committed(db, value):
    row = db('SELECT state,payload_json FROM server_remote_executions WHERE execution_id=%s',
             (value.intent.execution_id,))[0]
    assert row['state'] != 'completed' and row['payload_json'] is None
    assert db('SELECT evidence_id FROM server_processing_evidences WHERE execution_id=%s',
              (value.intent.execution_id,)) == []


@pytest.mark.parametrize('filters', [RemoteSearchFilters((6,), ('s4',)),
    RemoteSearchFilters((6,), ('s4',), True), RemoteSearchFilters((6, 7), ('s4',))])
def test_generation_and_verification_settle_then_save_and_replay_without_new_attempts(db, stores, pool, filters, capsys):
    value = chain(db, stores, pool, filters)
    payload = value.build().execute(value.intent, QUESTION, value.binding.context_id, filters=filters, top_k=1)
    assert payload['status'] == 'ok' and payload['verification_level'] == 'model_checked'
    assert payload['claims'] and payload['citations']
    assert all(c['quote_original'] == 'Synthetic original.' and c['quote_ko'] == '합성 원문이다.'
               and c['parse_revision_id'] == value.binding.parse_revision_id for c in payload['citations'])
    expected = [dict(stage=s, status='observed', actual_input_tokens=8, actual_output_tokens=4)
                for s in ('generation', 'semantic_verification')]
    assert attempts(db, value) == expected
    saved = db('SELECT evidence_id FROM server_processing_evidences WHERE execution_id=%s', (value.intent.execution_id,))
    assert {str(r['evidence_id']) for r in saved} == {c['evidence_id'] for c in payload['citations']}
    for _ in range(3):
        replay = read_existing_execution(PostgresRemoteExecutionStore(pool), value.lookup)
        assert replay.payload == payload
    assert value.post.call_count == 2 and value.embed.call_count == 1 and attempts(db, value) == expected
    logs = capsys.readouterr().err
    for canary in (QUESTION, 'Synthetic original.', '합성 원문이다.', 'synthetic-not-a-key'):
        assert canary not in logs


def test_semantic_call_budget_rejection_preserves_limit_and_does_not_commit_draft(db, stores, pool):
    filters = RemoteSearchFilters((6,), ('s4',))
    value = chain(db, stores, pool, filters, max_calls=1)
    with pytest.raises(DispatchBudgetExceeded, match='^DISPATCH_CALL_TOKEN_BUDGET_EXCEEDED$'):
        value.build().execute(value.intent, QUESTION, value.binding.context_id, filters=filters, top_k=1)
    assert value.post.call_count == 1
    assert attempts(db, value) == [dict(stage='generation', status='observed', actual_input_tokens=8, actual_output_tokens=4)]
    assert_no_answer_committed(db, value)


def test_time_budget_blocks_before_http_and_is_not_evidence_shortage(db, stores, pool):
    filters = RemoteSearchFilters((6,), ('s4',))
    value = chain(db, stores, pool, filters)
    db("UPDATE server_remote_executions SET created_at=clock_timestamp()-interval '11 minutes' WHERE execution_id=%s",
       (value.intent.execution_id,))
    with pytest.raises(DispatchBudgetExceeded, match='^DISPATCH_TIME_BUDGET_EXCEEDED$'):
        value.build().execute(value.intent, QUESTION, value.binding.context_id, filters=filters, top_k=1)
    value.post.assert_not_called()
    assert attempts(db, value) == []
    assert_no_answer_committed(db, value)


def test_timeout_unknown_survives_new_service_and_readonly_recovery(db, stores, pool, capsys):
    filters = RemoteSearchFilters((6,), ('s4',))
    value = chain(db, stores, pool, filters)
    value.post.side_effect = requests.Timeout('CANARY_PRIVATE_PROVIDER_ERROR')
    with pytest.raises(DispatchError, match='^DISPATCH_USAGE_UNKNOWN$'):
        value.build().execute(value.intent, QUESTION, value.binding.context_id, filters=filters, top_k=1)
    assert_no_answer_committed(db, value)
    replay = read_existing_execution(PostgresRemoteExecutionStore(pool), value.lookup)
    assert replay.payload is None and replay.budget_state == 'usage_unknown'
    with pytest.raises(DispatchError, match='^DISPATCH_NOT_AUTHORIZED$'):
        value.build('fresh').execute(value.intent, QUESTION, value.binding.context_id, filters=filters, top_k=1)
    assert value.post.call_count == 1
    assert attempts(db, value) == [dict(stage='generation', status='unknown', actual_input_tokens=None, actual_output_tokens=None)]
    assert_no_answer_committed(db, value)
    assert 'CANARY_PRIVATE_PROVIDER_ERROR' not in capsys.readouterr().err


@pytest.mark.parametrize('change', ['revoke', 'delete'])
def test_access_change_between_generation_and_verification_blocks_second_http_and_save(db, stores, pool, change):
    filters = RemoteSearchFilters((6,), ('s4',))
    value = chain(db, stores, pool, filters)
    def reply_then_change(url, **kw):
        response = value.reply(url, **kw)
        if change == 'revoke':
            db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE consent_id=%s', (value.intent.ai_consent_id,))
        else:
            PostgresRemoteDeletionStore(pool).request('alice', value.binding.paper_id, new_id())
        return response
    value.post.side_effect = reply_then_change
    with pytest.raises(DispatchError, match='^DISPATCH_NOT_AUTHORIZED$'):
        value.build().execute(value.intent, QUESTION, value.binding.context_id, filters=filters, top_k=1)
    assert value.post.call_count == 1
    assert attempts(db, value) == [dict(stage='generation', status='observed', actual_input_tokens=8, actual_output_tokens=4)]
    assert_no_answer_committed(db, value)
