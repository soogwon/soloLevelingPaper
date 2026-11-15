"""답변·근거의 원자 저장과 게시 리비전·실시간 lease 경계를 검증한다."""
# ruff: noqa: F811
from contextlib import contextmanager
from dataclasses import replace
import os

import pytest

from tests.integration.test_remote_answer_postgres import seeded, evaluator, FILTERS
from tests.integration.test_remote_processing_postgres import pool, db, stores, new_id  # noqa: F401
from solo_leveling.application.provider_dispatch import DispatchIntent
from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore, RemoteAnswerSaveRejected

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


def setup_answer(db, stores, pool):
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES ('alice','합성 사용자') ON CONFLICT DO NOTHING")
    _, _, binding = seeded(db, stores, pool)
    service, _, _ = evaluator(db, pool)
    prepared = service.prepare('합성 설명은?', binding.context_id, filters=FILTERS, top_k=1)
    storage, ai, execution = new_id(), new_id(), new_id()
    db('''INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version)
          VALUES (%s,'alice','storage','input','notice'),(%s,'alice','ai_provider','input','notice')''', (storage, ai))
    db('''INSERT INTO server_remote_executions
          (execution_id,owner_user_id,operation,request_key,input_hash,context_id,storage_consent_id,
           ai_consent_id,notice_version,state,budget_state,lease_token,lease_expires_at)
          VALUES (%s,'alice','ask',%s,'input',%s,%s,%s,'notice','running','available','lease',
                  clock_timestamp()+interval '5 minutes')''', (execution, execution, binding.context_id, storage, ai))
    return DispatchIntent('alice', execution, 'lease', 'input', storage, ai, 'notice'), prepared


def test_persists_answer_and_all_evidence_together(db, stores, pool):
    intent, prepared = setup_answer(db, stores, pool)
    payload = PostgresRemoteAnswerStore(pool).save(intent, prepared)
    row = db('SELECT state,payload_json FROM server_remote_executions WHERE execution_id=%s', (intent.execution_id,))[0]
    import json
    assert row['state'] == 'completed' and json.loads(row['payload_json']) == payload
    rows = db('SELECT evidence_id FROM server_processing_evidences WHERE execution_id=%s', (intent.execution_id,))
    assert {str(r['evidence_id']) for r in rows} == {e.evidence_id for e in prepared.evidence}


@pytest.mark.parametrize('field', ['translation_revision_id', 'embedding_set_id', 'publication_policy'])
def test_rejects_binding_outside_fixed_publication(db, stores, pool, field):
    intent, prepared = setup_answer(db, stores, pool)
    # 근거 없는 답변도 게시 집합 불일치로 완료되면 안 된다.
    from solo_leveling.domain.evidence_qa import AnswerResult, AnswerStatus
    response = replace(prepared.response, result=AnswerResult(AnswerStatus.INSUFFICIENT_EVIDENCE, '근거 부족', (), (), None))
    binding = replace(prepared.binding, **{field: 'partial' if field == 'publication_policy' else new_id()})
    with pytest.raises(RemoteAnswerSaveRejected):
        PostgresRemoteAnswerStore(pool).save(intent, replace(prepared, binding=binding, response=response, evidence=()))
    assert db('SELECT state FROM server_remote_executions WHERE execution_id=%s', (intent.execution_id,))[0]['state'] == 'running'


def test_expiry_during_save_rolls_back_evidence_and_completion(db, stores, pool):
    intent, prepared = setup_answer(db, stores, pool)

    class DelayedPool:
        @contextmanager
        def connection(self):
            with pool.connection() as conn:
                class Connection:
                    def execute(self, sql, params=()):
                        result = conn.execute(sql, params)
                        if sql.startswith('INSERT INTO server_processing_evidences'):
                            conn.execute('''UPDATE server_remote_executions
                                SET lease_expires_at=transaction_timestamp()+interval '0.1 seconds'
                                WHERE execution_id=%s''', (intent.execution_id,))
                            conn.execute('SELECT pg_sleep(0.2)')
                        return result
                yield Connection()

    with pytest.raises(RemoteAnswerSaveRejected, match='remote_answer_lease_lost'):
        PostgresRemoteAnswerStore(DelayedPool()).save(intent, prepared)
    assert db('SELECT evidence_id FROM server_processing_evidences WHERE execution_id=%s', (intent.execution_id,)) == []
    row = db('SELECT state,payload_json FROM server_remote_executions WHERE execution_id=%s', (intent.execution_id,))[0]
    assert row['state'] == 'running' and row['payload_json'] is None
