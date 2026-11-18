"""실제 검색·전송 원장·결과 저장 조립. 임베딩과 HTTP만 합성 대역이다."""
# ruff: noqa: F811
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import json
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores,new_id  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from tests.integration.test_remote_answer_postgres import FILTERS
from solo_leveling.application.evidence_qa.remote_search import QueryEmbedding
from solo_leveling.application.evidence_qa.remote_question_input import RemoteQuestionInput
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.remote_replay import ExecutionLookup
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
from solo_leveling.infrastructure.generation.semantic_verifier import SemanticSettings
from solo_leveling.workers.question_composition import QuestionExecutionFactory
from solo_leveling.workers.remote_question import RemoteQuestionRunner,PersistedQuestionExecutor

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr('requests.post',Mock(side_effect=AssertionError('실제 전송 금지')))


def prepare(db,stores,pool,*,fault=None,admitted=False,general=False):
    _,prepared=setup_answer(db,stores,pool)
    resolver=PostgresRemoteContextResolver(pool)
    binding=resolver.resolve('alice',prepared.binding.context_id)
    def respond(url,**kwargs):
        if fault=='network':raise TimeoutError('비공개 오류')
        data=json.loads(kwargs['json']['input'][0]['content'])
        evidence=data['evidence'][0]
        answer={'claims':[{'text':evidence['text_ko'],'evidence_ids':[evidence['evidence_id']],
            'supports':[{'evidence_id':evidence['evidence_id'],'quote_original':evidence['original_text']}]}]}
        if fault=='revoked':db("UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s",(ai,))
        return NS(status_code=200,close=Mock(),json=lambda:{'status':'completed','output':[{'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':json.dumps(answer)}]}],
            'usage':{'input_tokens':50,'output_tokens':25}})
    post=Mock(side_effect=respond)
    version=new_id()
    contract = None
    if general:
        from solo_leveling.workers.question_contract import QuestionContract
        contract = QuestionContract('synthetic',version,'test','synthetic-counter','synthetic-price',
            2000,100,50000,1,2,2200,4000,4000)
    factory=QuestionExecutionFactory(pool,generation=OpenAIGenerationSettings('synthetic-not-key','synthetic',5,100,True),
        semantic=SemanticSettings(),provider_profile_id=contract.profile if contract else 'synthetic-profile',
        embedding_runtime=NS(embed=lambda q,b:QueryEmbedding((1.,0.),b.model_fingerprint)),
        window_key='test',token_upper_bound=lambda model,payload:500,post=post,
        contract=contract,estimator_revision='synthetic-counter' if contract else None)
    policy=RemoteExecutionPolicy(version,60,10,1 if general else 2,2000,100 if general else 1000)
    config=factory.processing_config(version)
    if fault=='config':config['generation_model']='changed'
    request=RemoteQuestionInput.create(binding,'합성 설명은?',FILTERS,1,config)
    lookup=ExecutionLookup('alice','ask_paper',new_id(),request.input_hash,binding.context_id)
    storage,ai=new_id(),new_id()
    db("INSERT INTO server_dispatch_controls(control_id,generation,dispatch_enabled) VALUES ('provider_dispatch',1,true) ON CONFLICT(control_id) DO UPDATE SET dispatch_enabled=true")
    db("INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version) VALUES (%s,'alice','storage',%s,'notice'),(%s,'alice','ai_provider',%s,'notice')",(storage,request.input_hash,ai,request.input_hash))
    for scope,owner in [('owner','alice'),('service','service')]:
        db("INSERT INTO server_dispatch_budget_limits(scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens) VALUES (%s,%s,%s,'test',%s,1000)",(scope,owner,version,1 if fault=='budget' else 2000))
    store=PostgresRemoteExecutionStore(pool)
    if admitted:
        from solo_leveling.interfaces.http.remote_question_intake import PostgresQuestionIntake
        db("UPDATE server_dispatch_controls SET intake_enabled=true")
        saved=PostgresQuestionIntake(pool).submit('alice',lookup.request_key,request,storage,ai,'notice',policy=policy,window_key='test')
    else:
        saved=store.create(lookup,storage,ai,'notice',policy=policy,question_input=request)
    runner=RemoteQuestionRunner(store,PersistedQuestionExecutor(store,factory))
    return store,saved,runner,post


def test_search_dispatch_and_atomic_answer_replay(db,stores,pool):
    store,saved,runner,post=prepare(db,stores,pool)
    assert runner.run('alice',saved.execution_id)=='finished'
    row=store.lookup('alice','ask_paper',saved.lookup.request_key)
    assert row.state=='completed'
    payload=json.loads(db('SELECT payload_json FROM server_remote_executions WHERE execution_id=%s',(saved.execution_id,))[0]['payload_json'])
    assert payload['status']=='ok' and payload['citations']
    assert post.call_count==1
    assert db('SELECT status,actual_input_tokens FROM server_provider_attempts WHERE execution_id=%s',(saved.execution_id,))==[{'status':'observed','actual_input_tokens':50}]
    assert runner.run('alice',saved.execution_id)=='not_claimed' and post.call_count==1


@pytest.mark.parametrize('fault,calls',[('network',1),('config',0),('budget',0),('revoked',1)])
def test_failure_does_not_publish_or_retransmit(db,stores,pool,fault,calls):
    store,saved,runner,post=prepare(db,stores,pool,fault=fault)
    assert runner.run('alice',saved.execution_id) in ('limited','failed')
    row=store.lookup('alice','ask_paper',saved.lookup.request_key)
    assert row.state=='failed' and post.call_count==calls
    assert not db('SELECT evidence_id FROM server_processing_evidences WHERE execution_id=%s',(saved.execution_id,))
    assert runner.run('alice',saved.execution_id)=='not_claimed' and post.call_count==calls
    if fault=='network':assert row.usage_state=='unknown'


def test_cli_queue_admission_search_answer_and_budget_settlement(db,stores,pool,monkeypatch):
    from contextlib import contextmanager
    from solo_leveling.interfaces.cli import question_worker
    store,saved,runner,post=prepare(db,stores,pool,admitted=True)
    @contextmanager
    def factory():
        yield runner
    monkeypatch.setattr(question_worker.importlib,'import_module',lambda name:NS(factory=factory))
    assert question_worker.main(['--execute','--runtime','approved:factory','--max-jobs','1'])==0
    assert store.lookup('alice','ask_paper',saved.lookup.request_key).state=='completed'
    assert post.call_count==1 and runner.run_once() is None
    rows=db('''SELECT b.reserved_input_tokens,b.reserved_output_tokens,b.consumed_input_tokens,b.consumed_output_tokens
        FROM server_dispatch_budget_limits b JOIN server_question_budget_reservations r
        ON r.policy_version=b.policy_version AND r.window_key=b.window_key WHERE r.execution_id=%s''',(saved.execution_id,))
    assert len(rows)==2
    assert all(row==dict(reserved_input_tokens=0,reserved_output_tokens=0,consumed_input_tokens=50,consumed_output_tokens=25) for row in rows)


@pytest.mark.parametrize('scope', [None, 'owner', 'service'])
def test_general_contract_checks_locked_budget_limits_before_http(db, stores, pool, scope):
    store, saved, runner, post = prepare(db, stores, pool, general=True, admitted=True)
    if scope:
        db("UPDATE server_dispatch_budget_limits SET max_input_tokens=max_input_tokens+1 WHERE policy_version=%s AND scope_type=%s",
           (store.policy('alice', saved.execution_id).version, scope))
    status = runner.run('alice', saved.execution_id)
    assert status == ('failed' if scope else 'finished')
    assert post.call_count == (0 if scope else 1)
    assert runner.run('alice', saved.execution_id) == 'not_claimed'
    if scope:
        assert not db('SELECT attempt_id FROM server_provider_attempts WHERE execution_id=%s', (saved.execution_id,))
    else:
        assert store.lookup('alice', 'ask_paper', saved.lookup.request_key).state == 'completed'


def test_general_unknown_does_not_refund_or_retry(db, stores, pool):
    store, saved, runner, post = prepare(db, stores, pool, general=True, admitted=True, fault='network')
    assert runner.run('alice', saved.execution_id) == 'failed'
    assert runner.run('alice', saved.execution_id) == 'not_claimed'
    assert post.call_count == 1
    assert db('SELECT status,reserved_input_tokens,reserved_output_tokens FROM server_provider_attempts WHERE execution_id=%s',
        (saved.execution_id,)) == [dict(status='unknown', reserved_input_tokens=500, reserved_output_tokens=100)]
