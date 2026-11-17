"""정책 불변·기한·이전 worker의 늦은 종결을 실제 DB에서 검증한다."""
# ruff: noqa: F811
import os
import time
from dataclasses import replace
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores,new_id  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.remote_replay import ExecutionLookup
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore,RemoteAnswerSaveRejected
from solo_leveling.workers.remote_question import RemoteQuestionRunner

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='실제 PostgreSQL DSN 필요')


def new_execution(db,stores,pool,seconds=60):
    old,prepared=setup_answer(db,stores,pool)
    db("INSERT INTO server_dispatch_controls(control_id,generation,dispatch_enabled) VALUES ('provider_dispatch',1,true) ON CONFLICT(control_id) DO UPDATE SET dispatch_enabled=true")
    store=PostgresRemoteExecutionStore(pool)
    lookup=ExecutionLookup('alice','ask',new_id(),'input',prepared.binding.context_id)
    policy=RemoteExecutionPolicy('test-v1',seconds,min(seconds,10),1,1000,1000)
    saved=store.create(lookup,old.storage_consent_id,old.ai_consent_id,'notice',policy=policy)
    return store,lookup,saved,policy,prepared,old


def test_policy_pinned_and_stale_worker_cannot_finish(db,stores,pool):
    import psycopg
    store,lookup,saved,policy,prepared,old=new_execution(db,stores,pool)
    again=store.create(lookup,old.storage_consent_id,old.ai_consent_id,'notice',policy=replace(policy,max_calls=9))
    assert again.execution_id==saved.execution_id
    assert store.policy('alice',saved.execution_id)==policy
    with pytest.raises(psycopg.errors.CheckViolation):
        db("UPDATE server_remote_executions SET deadline_at=deadline_at+interval '1 hour' WHERE execution_id=%s",(saved.execution_id,))
    intent=store.claim('alice',saved.execution_id)
    assert store.heartbeat(intent)
    db("UPDATE server_remote_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=%s",(saved.execution_id,))
    newer=store.claim('alice',saved.execution_id)
    assert newer.lease_token!=intent.lease_token
    assert not store.fail(intent,'internal_failed')
    assert not store.heartbeat(intent)
    with pytest.raises(RemoteAnswerSaveRejected): PostgresRemoteAnswerStore(pool).save(intent,prepared)
    PostgresRemoteAnswerStore(pool).save(newer,prepared)
    assert not store.fail(newer,'provider_failed')
    assert store.lookup('alice','ask',lookup.request_key).state=='completed'


def test_deadline_blocks_late_completion_and_does_not_extend(db,stores,pool):
    store,lookup,saved,policy,prepared,_=new_execution(db,stores,pool,seconds=1)
    intent=store.claim('alice',saved.execution_id)
    assert store.heartbeat(intent,lease_seconds=60)
    time.sleep(1.05)
    assert not store.heartbeat(intent)
    with pytest.raises(RemoteAnswerSaveRejected): PostgresRemoteAnswerStore(pool).save(intent,prepared)
    assert store.expire('alice',saved.execution_id)
    row=store.lookup('alice','ask',lookup.request_key)
    assert row.state=='failed' and row.terminal_reason=='deadline_exceeded'


def test_runner_replays_without_second_execution_and_cancellation_wins(db,stores,pool):
    store,lookup,saved,policy,prepared,_=new_execution(db,stores,pool)
    calls=[]
    def execute(intent,policy,allowed):
        calls.append(intent)
        assert allowed()
        store.request_cancel('alice',saved.execution_id)
        PostgresRemoteAnswerStore(pool).save(intent,prepared)
    runner=RemoteQuestionRunner(store,execute)
    assert runner.run('alice',saved.execution_id)=='failed'
    assert runner.run('alice',saved.execution_id)=='not_claimed'
    assert len(calls)==1
    assert store.confirm_cancel('alice',saved.execution_id)
    assert store.lookup('alice','ask',lookup.request_key).state=='cancelled'


def test_revoked_consent_blocks_late_publication(db,stores,pool):
    store,lookup,saved,policy,prepared,_=new_execution(db,stores,pool)
    intent=store.claim('alice',saved.execution_id)
    db("UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE owner_user_id=%s AND consent_id=%s",('alice',intent.ai_consent_id))
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation, match='execution_scope_revoked'):
        PostgresRemoteAnswerStore(pool).save(intent,prepared)
    assert not db('SELECT evidence_id FROM server_processing_evidences WHERE execution_id=%s',(saved.execution_id,))
    assert store.fail(intent,'scope_revoked')
    assert store.lookup('alice','ask',lookup.request_key).terminal_reason=='scope_revoked'


def test_persisted_limits_override_caller_and_usage_does_not_revive_failure(db,stores,pool):
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    from solo_leveling.application.provider_dispatch import DispatchError
    from solo_leveling.infrastructure.generation.execution_budget import ExecutionBudget
    from solo_leveling.infrastructure.generation.gated_responses import PreparedProviderAttempt
    from solo_leveling.infrastructure.generation.persisted_dispatch import create_persisted_responses_transport
    from tests.integration.test_persisted_dispatch_postgres import PAYLOAD
    store,lookup,saved,policy,prepared,_=new_execution(db,stores,pool)
    intent=store.claim('alice',saved.execution_id)
    budget_policy=new_id()
    for scope,scope_id in [('service','service'),('owner','alice')]:
        db("""INSERT INTO server_dispatch_budget_limits
            (scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
            VALUES (%s,%s,%s,'window',10000,10000)""",(scope,scope_id,budget_policy))
    response=NS(status_code=200,json=lambda:{'output':[]},close=Mock())
    post=Mock(return_value=response)
    settings=NS(allow_external_api=True,local_only=False,model='synthetic',max_output_tokens=20,
                api_key='synthetic-not-a-key',timeout_seconds=100)
    def build(key):
        return create_persisted_responses_transport(pool,intent,budget_policy,'window',
            lambda stage,payload,digest:PreparedProviderAttempt(key,digest,100),post=post,
            execution_budget=ExecutionBudget(999,999999,999999,999999))
    with pytest.raises(DispatchError,match='DISPATCH_USAGE_UNKNOWN'):
        build('one').send(settings,PAYLOAD,'generation')
    assert post.call_args.kwargs['timeout'][1]==policy.stage_timeout_seconds
    assert store.fail(intent,'budget_exhausted')
    row=store.lookup('alice','ask',lookup.request_key)
    assert (row.state,row.budget_state,row.usage_state,row.terminal_reason)==('failed','limited','unknown','budget_exhausted')
    attempt=db('SELECT attempt_id FROM server_provider_attempts WHERE execution_id=%s',(saved.execution_id,))[0]
    build('unused').dispatch.ledger.observe(str(attempt['attempt_id']),8,4)
    row=store.lookup('alice','ask',lookup.request_key)
    assert (row.state,row.budget_state,row.usage_state,row.terminal_reason)==('failed','limited','known','budget_exhausted')
    with pytest.raises(DispatchError): build('two').send(settings,PAYLOAD,'repair')
    assert post.call_count==1


def test_persisted_call_limit_survives_new_transport(db,stores,pool):
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    from solo_leveling.application.provider_dispatch import DispatchBudgetExceeded
    from solo_leveling.infrastructure.generation.execution_budget import ExecutionBudget
    from solo_leveling.infrastructure.generation.gated_responses import PreparedProviderAttempt
    from solo_leveling.infrastructure.generation.persisted_dispatch import create_persisted_responses_transport
    from tests.integration.test_persisted_dispatch_postgres import PAYLOAD,SETTINGS
    store,lookup,saved,policy,prepared,_=new_execution(db,stores,pool)
    intent=store.claim('alice',saved.execution_id)
    budget_policy=new_id()
    for scope,scope_id in [('service','service'),('owner','alice')]:
        db("""INSERT INTO server_dispatch_budget_limits
            (scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
            VALUES (%s,%s,%s,'window',10000,10000)""",(scope,scope_id,budget_policy))
    post=Mock(return_value=NS(status_code=200,json=lambda:{'output':[],'usage':{'input_tokens':8,'output_tokens':4}},close=Mock()))
    def build(key):
        return create_persisted_responses_transport(pool,intent,budget_policy,'window',
            lambda stage,payload,digest:PreparedProviderAttempt(key,digest,100),post=post,
            execution_budget=ExecutionBudget(999,999999,999999,999999))
    build('one').send(SETTINGS,PAYLOAD,'generation')
    with pytest.raises(DispatchBudgetExceeded): build('two').send(SETTINGS,PAYLOAD,'repair')
    assert post.call_count==1
