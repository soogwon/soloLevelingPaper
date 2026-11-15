"""실제 검색·source·저장 조립. 인증·임베딩·생성은 합성 대역이다."""
# ruff: noqa: F811
from dataclasses import replace
from types import SimpleNamespace as NS
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from tests.integration.test_remote_answer_postgres import FILTERS
from tests.unit.test_persisted_remote_answer import CONFIG
from solo_leveling.application.evidence_qa.remote_input import question_input_hash
from solo_leveling.application.evidence_qa.remote_search import QueryEmbedding
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.infrastructure.generation.fake_generator import FakeClaimGenerator
from solo_leveling.infrastructure.retrieval.remote_answer_factory import create_persisted_remote_answer_service, RemoteAnswerExecutionError

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='실제 PostgreSQL DSN 필요')


@pytest.mark.parametrize('expire', [False, True])
def test_real_search_source_and_atomic_save(pool,db,stores,expire):
    intent, prepared=setup_answer(db,stores,pool)
    digest=question_input_hash(prepared.binding,'합성 질문',FILTERS,1,CONFIG)
    db('UPDATE server_remote_executions SET input_hash=%s WHERE execution_id=%s',(digest,intent.execution_id))
    intent=replace(intent,input_hash=digest)
    resolver=PostgresRemoteContextResolver(pool)
    generator=FakeClaimGenerator()
    if expire:
        original=generator.generate_claims
        def generate(*args):
            result=original(*args)
            db("UPDATE server_remote_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=%s",(intent.execution_id,))
            return result
        generator.generate_claims=generate
    service=create_persisted_remote_answer_service(pool,lambda ctx:resolver.resolve('alice',ctx),
        NS(embed=lambda q,b:QueryEmbedding((1.,0.),b.model_fingerprint)),generator,CONFIG)
    service._preparation.prepare('합성 질문',prepared.binding.context_id,filters=FILTERS,top_k=1)
    if expire:
        with pytest.raises(RemoteAnswerExecutionError,match='REMOTE_ANSWER_SAVE_FAILED'):
            service.execute(intent,'합성 질문',prepared.binding.context_id,filters=FILTERS,top_k=1)
        assert db('SELECT evidence_id FROM server_processing_evidences WHERE execution_id=%s',(intent.execution_id,)) == []
    else:
        result=service.execute(intent,'합성 질문',prepared.binding.context_id,filters=FILTERS,top_k=1)
        assert result['status']=='ok' and result['citations']
        assert db('SELECT state FROM server_remote_executions WHERE execution_id=%s',(intent.execution_id,))[0]['state']=='completed'
