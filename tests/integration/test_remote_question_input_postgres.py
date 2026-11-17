"""질문 입력과 실행의 원자 접수 및 재시작·stale lease 거부를 검증한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores,new_id  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from solo_leveling.application.evidence_qa.remote_input import CONFIG_KEYS
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters
from solo_leveling.application.evidence_qa.remote_question_input import RemoteQuestionInput
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.remote_replay import ExecutionLookup
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore,RemoteExecutionConflict
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='실제 PostgreSQL DSN 필요')


def setup(db,stores,pool):
    _,prepared=setup_answer(db,stores,pool)
    binding=PostgresRemoteContextResolver(pool).resolve('alice',prepared.binding.context_id)
    config={k:'test-v1' for k in CONFIG_KEYS}
    request=RemoteQuestionInput.create(binding,'합성 질문',RemoteSearchFilters((1,),(),True),1,config)
    lookup=ExecutionLookup('alice','ask_paper',new_id(),request.input_hash,binding.context_id)
    storage,ai=new_id(),new_id()
    db("INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version) VALUES (%s,'alice','storage',%s,'notice'),(%s,'alice','ai_provider',%s,'notice')",(storage,request.input_hash,ai,request.input_hash))
    db("INSERT INTO server_dispatch_controls(control_id,generation,dispatch_enabled) VALUES ('provider_dispatch',1,true) ON CONFLICT(control_id) DO UPDATE SET dispatch_enabled=true")
    policy=RemoteExecutionPolicy('test-v1',60,10,1,1000,1000)
    return request,lookup,storage,ai,policy


def test_concurrent_submission_restarts_with_same_input_and_one_row(db,stores,pool):
    request,lookup,storage,ai,policy=setup(db,stores,pool)
    def submit(_):
        return PostgresRemoteExecutionStore(pool).create(lookup,storage,ai,'notice',policy=policy,question_input=request)
    with ThreadPoolExecutor(max_workers=4) as threads: rows=list(threads.map(submit,range(8)))
    assert len({r.execution_id for r in rows})==1
    store=PostgresRemoteExecutionStore(pool)
    intent=store.claim('alice',rows[0].execution_id)
    restored=store.question_input(intent)
    assert restored.unpack()==request.unpack() and restored.input_hash==lookup.input_hash
    assert '합성 질문' not in repr(restored)
    assert store.question_input(replace(intent,owner_user_id='bob')) is None
    db("UPDATE server_remote_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=%s",(intent.execution_id,))
    newer=store.claim('alice',intent.execution_id)
    assert store.question_input(intent) is None
    assert store.question_input(newer).unpack()==request.unpack()
    assert len(db('SELECT execution_id FROM server_remote_executions WHERE operation=%s AND request_key=%s',('ask_paper',lookup.request_key)))==1
    assert not db('SELECT attempt_id FROM server_provider_attempts')


def test_snapshot_immutable_and_changed_question_conflicts(db,stores,pool):
    import psycopg
    request,lookup,storage,ai,policy=setup(db,stores,pool)
    store=PostgresRemoteExecutionStore(pool)
    row=store.create(lookup,storage,ai,'notice',policy=policy,question_input=request)
    changed=RemoteQuestionInput.create(request.unpack()[0],'다른 질문',*request.unpack()[2:])
    with pytest.raises(RemoteExecutionConflict):
        store.create(replace(lookup,input_hash=changed.input_hash),storage,ai,'notice',policy=policy,question_input=changed)
    with pytest.raises(psycopg.errors.CheckViolation):
        db("UPDATE server_remote_executions SET question_snapshot=%s::jsonb WHERE execution_id=%s",(changed.encoded,row.execution_id))
    assert db('SELECT question_snapshot FROM server_remote_executions WHERE execution_id=%s',(row.execution_id,))[0]['question_snapshot']==json.loads(request.encoded)


def test_revoked_consent_blocks_input_read_and_new_admission(db,stores,pool):
    request,lookup,storage,ai,policy=setup(db,stores,pool)
    store=PostgresRemoteExecutionStore(pool)
    row=store.create(lookup,storage,ai,'notice',policy=policy,question_input=request)
    intent=store.claim('alice',row.execution_id)
    db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s',(ai,))
    assert store.question_input(intent) is None
    assert store.create(replace(lookup,request_key=new_id()),storage,ai,'notice',policy=policy,question_input=request) is None


def test_admission_failure_rolls_back_snapshot(db,stores,pool):
    request,lookup,storage,ai,policy=setup(db,stores,pool)
    store=PostgresRemoteExecutionStore(pool,admit=lambda *args:False)
    assert store.create(lookup,storage,ai,'notice',policy=policy,question_input=request) is None
    assert not db("SELECT execution_id FROM server_remote_executions WHERE operation='ask_paper' AND request_key=%s",(lookup.request_key,))


def test_wrong_hash_and_policy_are_rejected_before_write(db,stores,pool):
    request,lookup,storage,ai,policy=setup(db,stores,pool)
    store=PostgresRemoteExecutionStore(pool)
    for wrong_lookup,wrong_policy in [(replace(lookup,input_hash='wrong'),policy),(lookup,replace(policy,version='other'))]:
        with pytest.raises(ValueError):store.create(wrong_lookup,storage,ai,'notice',policy=wrong_policy,question_input=request)
    assert not db('SELECT execution_id FROM server_remote_executions WHERE request_key=%s',(lookup.request_key,))


def test_deleted_paper_blocks_restored_input(db,stores,pool):
    request,lookup,storage,ai,policy=setup(db,stores,pool)
    store=PostgresRemoteExecutionStore(pool)
    row=store.create(lookup,storage,ai,'notice',policy=policy,question_input=request)
    intent=store.claim('alice',row.execution_id)
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s',(request.unpack()[0].paper_id,))
    assert store.question_input(intent) is None


def test_legacy_execution_is_not_reinterpreted_as_stored_question(db,stores,pool):
    request,lookup,storage,ai,policy=setup(db,stores,pool)
    store=PostgresRemoteExecutionStore(pool)
    row=store.create(lookup,storage,ai,'notice',policy=policy)
    with pytest.raises(RemoteExecutionConflict):store.create(lookup,storage,ai,'notice',policy=policy,question_input=request)
    intent=store.claim('alice',row.execution_id)
    assert store.question_input(intent) is None
