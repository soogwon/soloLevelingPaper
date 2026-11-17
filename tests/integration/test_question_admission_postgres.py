"""질문 접수 예산의 동시 예약·실제 정산·미확정 보존을 검증한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores,new_id  # noqa: F401
from tests.integration.test_remote_question_input_postgres import setup
from solo_leveling.interfaces.http.remote_question_intake import PostgresQuestionIntake
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.infrastructure.generation.persisted_dispatch import create_persisted_responses_transport
from solo_leveling.infrastructure.generation.gated_responses import PreparedProviderAttempt
from solo_leveling.application.provider_dispatch import DispatchError

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


def configured(db,stores,pool):
    request,lookup,storage,ai,policy=setup(db,stores,pool)
    version=new_id();policy=replace(policy,version=version)
    args=list(request.unpack());args[-1]['budget_policy_version']=version
    request=type(request).create(*args)
    db("UPDATE server_remote_consents SET input_hash=%s WHERE consent_id IN (%s,%s)",(request.input_hash,storage,ai))
    db("UPDATE server_dispatch_controls SET intake_enabled=true")
    for scope,owner in [('service','service'),('owner','alice')]:
        db("INSERT INTO server_dispatch_budget_limits(scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens) VALUES (%s,%s,%s,'test',1000,1000)",(scope,owner,version))
    def submit(key):return PostgresQuestionIntake(pool).submit('alice',key,request,storage,ai,'notice',policy=policy,window_key='test')
    return submit,policy


def limits(db,policy):
    return db("SELECT reserved_input_tokens,reserved_output_tokens,consumed_input_tokens,consumed_output_tokens FROM server_dispatch_budget_limits WHERE policy_version=%s ORDER BY scope_type",(policy.version,))


def test_concurrent_different_keys_share_owner_service_ceiling(db,stores,pool):
    submit,policy=configured(db,stores,pool)
    with ThreadPoolExecutor(max_workers=4) as workers:rows=list(workers.map(submit,[new_id() for _ in range(4)]))
    assert sum(r is not None for r in rows)==1
    assert len(db('SELECT execution_id FROM server_question_budget_reservations WHERE policy_version=%s',(policy.version,)))==1
    assert all(r['reserved_input_tokens']==1000 for r in limits(db,policy))


def test_same_key_recovers_single_reservation_and_cancel_returns_unused(db,stores,pool):
    submit,policy=configured(db,stores,pool);key=new_id()
    with ThreadPoolExecutor(max_workers=4) as workers:rows=list(workers.map(submit,[key]*4))
    assert len({r.execution_id for r in rows})==1
    store=PostgresRemoteExecutionStore(pool)
    store.request_cancel('alice',rows[0].execution_id)
    assert all(r['reserved_input_tokens']==0 for r in limits(db,policy))
    assert submit(key).execution_id==rows[0].execution_id
    assert all(r['reserved_input_tokens']==0 for r in limits(db,policy))


@pytest.mark.parametrize('uncertain',[False,True])
def test_attempt_spends_job_allocation_without_double_reservation(db,stores,pool,uncertain):
    submit,policy=configured(db,stores,pool);saved=submit(new_id())
    store=PostgresRemoteExecutionStore(pool);intent=store.claim('alice',saved.execution_id)
    post=Mock(side_effect=TimeoutError('비공개') if uncertain else None,
        return_value=NS(status_code=200,json=lambda:{'usage':{'input_tokens':20,'output_tokens':10}},close=Mock()))
    transport=create_persisted_responses_transport(pool,intent,policy.version,'test',
        lambda stage,payload,digest:PreparedProviderAttempt('one',digest,100),post=post)
    settings=NS(allow_external_api=True,local_only=False,model='synthetic',max_output_tokens=100,api_key='synthetic',timeout_seconds=5)
    payload={'model':'synthetic','max_output_tokens':100,'store':False,'input':'합성'}
    if uncertain:
        with pytest.raises(DispatchError):transport.send(settings,payload,'generation')
    else:transport.send(settings,payload,'generation')
    assert post.call_count==1
    assert store.fail(intent,'provider_failed')
    for row in limits(db,policy):
        assert row['reserved_input_tokens']==(100 if uncertain else 0)
        assert row['consumed_input_tokens']==(0 if uncertain else 20)
    assert store.lookup('alice','ask_paper',saved.lookup.request_key).usage_state==('unknown' if uncertain else 'known')


def test_wrong_window_never_transmits(db,stores,pool):
    submit,policy=configured(db,stores,pool);saved=submit(new_id())
    store=PostgresRemoteExecutionStore(pool);intent=store.claim('alice',saved.execution_id)
    post=Mock()
    transport=create_persisted_responses_transport(pool,intent,policy.version,'different',
        lambda stage,payload,digest:PreparedProviderAttempt('one',digest,100),post=post)
    with pytest.raises(DispatchError):transport.send(NS(allow_external_api=True,local_only=False,model='synthetic',max_output_tokens=100,api_key='synthetic',timeout_seconds=5),
        {'model':'synthetic','max_output_tokens':100,'store':False},'generation')
    post.assert_not_called()


def test_intake_disabled_rolls_back_execution_and_reservation(db,stores,pool):
    submit,policy=configured(db,stores,pool)
    db("UPDATE server_dispatch_controls SET intake_enabled=false")
    assert submit(new_id()) is None
    assert not db('SELECT execution_id FROM server_question_budget_reservations WHERE policy_version=%s',(policy.version,))
    assert not db("SELECT execution_id FROM server_remote_executions WHERE policy_snapshot->>'version'=%s",(policy.version,))
    assert all(r['reserved_input_tokens']==0 for r in limits(db,policy))


def test_deleted_execution_returns_only_unused_reservation(db,stores,pool):
    submit,policy=configured(db,stores,pool);saved=submit(new_id())
    db("DELETE FROM server_remote_executions WHERE owner_user_id='alice' AND execution_id=%s",(saved.execution_id,))
    assert all(r['reserved_input_tokens']==0 for r in limits(db,policy))
    assert not db('SELECT execution_id FROM server_question_budget_reservations WHERE policy_version=%s',(policy.version,))
