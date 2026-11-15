"""B 검색·실제 출처·답변 저장·읽기 복구 연결. 인증·모델은 합성 대역이다."""
# ruff: noqa: F811
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores,new_id  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from tests.integration.test_remote_answer_postgres import seeded
from tests.unit.test_persisted_remote_answer import CONFIG
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters
from solo_leveling.application.evidence_qa.remote_input import question_input_hash
from solo_leveling.application.evidence_qa.remote_search import QueryEmbedding
from solo_leveling.application.remote_replay import ExecutionLookup,read_existing_execution
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore
from solo_leveling.infrastructure.generation.fake_generator import FakeClaimGenerator
from solo_leveling.infrastructure.retrieval.remote_answer_factory import create_persisted_remote_answer_service,RemoteAnswerExecutionError
pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='실제 PostgreSQL DSN 필요')
QUESTION='합성 검증 질문 CANARY_397_QUESTION'


def fixture(db,stores,pool,filters,owner='alice'):
    intent,prepared=setup_answer(db,stores,pool)
    digest=question_input_hash(prepared.binding,QUESTION,filters,1,CONFIG)
    db('UPDATE server_remote_executions SET input_hash=%s WHERE execution_id=%s',(digest,intent.execution_id))
    intent=replace(intent,input_hash=digest)
    resolver=PostgresRemoteContextResolver(pool)
    embed=Mock(side_effect=lambda q,b:QueryEmbedding((1.,0.),b.model_fingerprint))
    generate=Mock(wraps=FakeClaimGenerator().generate_claims)
    service=create_persisted_remote_answer_service(pool,lambda ctx:resolver.resolve(owner,ctx),NS(embed=embed),NS(generate_claims=generate),CONFIG)
    return intent,prepared.binding,service,embed,generate


@pytest.mark.parametrize('filters',[RemoteSearchFilters((6,),('s4',)),RemoteSearchFilters((6,),('s4',),True),RemoteSearchFilters((6,7),('s4',))])
def test_filtered_answer_survives_republication_and_replay_without_generation(db,stores,pool,filters,capsys):
    intent,binding,service,embed,generate=fixture(db,stores,pool,filters)
    seeded(db,stores,pool,'bob')
    payload=service.execute(intent,QUESTION,binding.context_id,filters=filters,top_k=1)
    assert payload['status']=='ok' and payload['citations']
    rows=db('SELECT chunk_id,pdf_page,section_id FROM server_processing_chunks WHERE parse_revision_id=%s',(binding.parse_revision_id,))
    allowed={str(r['chunk_id']) for r in rows if r['pdf_page'] in filters.pdf_pages and r['section_id']=='s4'}
    assert {c['chunk_id'] for c in payload['citations']} <= allowed
    assert all(c['parse_revision_id']==binding.parse_revision_id and c['quote_original']=='Synthetic original.' for c in payload['citations'])
    _,_,new=seeded(db,stores,pool,paper=binding.paper_id)
    assert new.publication_id != binding.publication_id
    lookup=ExecutionLookup('alice','ask',intent.execution_id,intent.input_hash,binding.context_id)
    calls=(embed.call_count,generate.call_count)
    for _ in range(3):
        result=read_existing_execution(PostgresRemoteExecutionStore(pool),lookup)
        assert result.payload==payload and result.execution_id==intent.execution_id
    assert (embed.call_count,generate.call_count)==calls==(1,1)
    assert read_existing_execution(PostgresRemoteExecutionStore(pool),replace(lookup,owner_user_id='bob')) is None
    assert db('SELECT attempt_id FROM server_provider_attempts WHERE execution_id=%s',(intent.execution_id,))==[]
    logs=capsys.readouterr().err
    assert QUESTION not in logs and 'Synthetic original.' not in logs and '합성 원문이다.' not in logs


def test_foreign_authenticated_owner_stops_before_embedding(db,stores,pool):
    filters=RemoteSearchFilters((6,),('s4',))
    intent,binding,service,embed,generate=fixture(db,stores,pool,filters,owner='bob')
    with pytest.raises(RemoteAnswerExecutionError,match='REMOTE_EXECUTION_SCOPE_LOST'):
        service.execute(intent,QUESTION,binding.context_id,filters=filters,top_k=1)
    embed.assert_not_called(); generate.assert_not_called()


def test_deletion_after_generation_rejects_save_and_replay(db,stores,pool,capsys):
    filters=RemoteSearchFilters((6,),('s4',))
    intent,binding,service,embed,generate=fixture(db,stores,pool,filters)
    def deleted(*args):
        result=FakeClaimGenerator().generate_claims(*args)
        PostgresRemoteDeletionStore(pool).request('alice',binding.paper_id,new_id())
        return result
    generate.side_effect=deleted
    with pytest.raises(RemoteAnswerExecutionError,match='REMOTE_ANSWER_SAVE_FAILED'):
        service.execute(intent,QUESTION,binding.context_id,filters=filters,top_k=1)
    assert db('SELECT evidence_id FROM server_processing_evidences WHERE execution_id=%s',(intent.execution_id,))==[]
    lookup=ExecutionLookup('alice','ask',intent.execution_id,intent.input_hash,binding.context_id)
    assert read_existing_execution(PostgresRemoteExecutionStore(pool),lookup) is None
    logs=capsys.readouterr().err
    assert QUESTION not in logs and 'Synthetic original.' not in logs
