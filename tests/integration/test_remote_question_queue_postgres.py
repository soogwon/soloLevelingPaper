"""동시 queue 선택과 중단된 전송의 자동 재호출 금지를 실제 DB에서 확인한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
import time
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores, new_id  # noqa: F401
from tests.integration.test_remote_question_input_postgres import setup
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.workers.remote_question import RemoteQuestionRunner, PersistedQuestionExecutor

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


@pytest.fixture(autouse=True)
def isolate_queue(db):
    db("UPDATE server_remote_executions SET state='failed',finished_at=clock_timestamp(),lease_token=NULL,lease_expires_at=NULL WHERE question_snapshot IS NOT NULL AND state IN ('queued','running')")


def submit(db,stores,pool,seconds=None):
    request,lookup,storage,ai,policy=setup(db,stores,pool)
    if seconds:policy=replace(policy,timeout_seconds=seconds,stage_timeout_seconds=seconds)
    store=PostgresRemoteExecutionStore(pool)
    saved=store.create(lookup,storage,ai,'notice',policy=policy,question_input=request)
    return store,saved,request,policy


def test_concurrent_workers_claim_distinct_rows(db,stores,pool):
    items=[submit(db,stores,pool) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as workers:
        results=list(workers.map(lambda _:PostgresRemoteExecutionStore(pool).claim_next_question(),range(8)))
    claimed=[r[1] for r in results if r]
    assert len(claimed)==4 and len({i.execution_id for i in claimed})==4
    assert {i.execution_id for i in claimed}=={r.execution_id for _,r,_,_ in items}


def test_restart_restores_input_and_rejects_previous_worker(db,stores,pool):
    store,saved,request,policy=submit(db,stores,pool)
    old=store.claim_next_question()[1]
    db("UPDATE server_remote_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=%s",(old.execution_id,))
    observed=[]
    fresh=PostgresRemoteExecutionStore(pool)
    def execute(intent,pinned,allowed,restored):
        assert allowed() and pinned==policy and restored.unpack()==request.unpack()
        assert fresh.question_input(old) is None and not fresh.fail(old,'internal_failed')
        observed.append(intent)
        assert fresh.fail(intent,'internal_failed')
    runner=RemoteQuestionRunner(fresh,PersistedQuestionExecutor(fresh,execute))
    assert runner.run_once()=='finished'
    assert runner.run_once() is None
    assert len(observed)==1 and observed[0].lease_token!=old.lease_token


@pytest.mark.parametrize('block',['consent','delete','dispatch'])
def test_revocation_never_executes_and_does_not_poison_queue(db,stores,pool,block):
    store,saved,request,_=submit(db,stores,pool)
    if block=='consent':db("UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE owner_user_id='alice' AND input_hash=%s",(request.input_hash,))
    if block=='delete':db("UPDATE server_papers SET deleted_at=clock_timestamp() WHERE owner_user_id='alice' AND paper_id=%s",(request.unpack()[0].paper_id,))
    if block=='dispatch':db("UPDATE server_dispatch_controls SET dispatch_enabled=false")
    assert store.claim_next_question()==('blocked',None,None)
    assert store.claim_next_question() is None
    assert store.lookup('alice','ask_paper',saved.lookup.request_key).terminal_reason=='scope_revoked'


def test_expired_deadline_is_terminal_without_generation(db,stores,pool):
    store,saved,_,_=submit(db,stores,pool,seconds=1)
    time.sleep(1.05)
    assert store.claim_next_question()==('expired',None,None)
    assert store.lookup('alice','ask_paper',saved.lookup.request_key).terminal_reason=='deadline_exceeded'


@pytest.mark.parametrize('status',['reserved','unknown','observed'])
def test_previous_attempt_never_auto_retransmits(db,stores,pool,status):
    store,saved,_,_=submit(db,stores,pool)
    intent=store.claim_next_question()[1]
    db("""INSERT INTO server_provider_attempts(attempt_id,owner_user_id,execution_id,lease_token,input_hash,
        storage_consent_id,ai_consent_id,notice_version,dispatch_key,stage,policy_version,window_key,
        control_generation,reserved_input_tokens,reserved_output_tokens,status,actual_input_tokens,actual_output_tokens,settled_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'one','generation','test-v1','test',1,100,100,%s,%s,%s,
        CASE WHEN %s='reserved' THEN NULL ELSE clock_timestamp() END)""",
        (new_id(),intent.owner_user_id,intent.execution_id,intent.lease_token,intent.input_hash,intent.storage_consent_id,intent.ai_consent_id,intent.notice_version,status,1 if status=='observed' else None,1 if status=='observed' else None,status))
    db("UPDATE server_remote_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=%s",(intent.execution_id,))
    assert store.claim_next_question()==('blocked',None,None)
    row=store.lookup('alice','ask_paper',saved.lookup.request_key)
    assert row.state=='failed' and row.terminal_reason=='provider_failed'
    assert row.usage_state==('known' if status=='observed' else 'unknown')
    assert not store.fail(intent,'internal_failed')
    assert store.claim_next_question() is None
