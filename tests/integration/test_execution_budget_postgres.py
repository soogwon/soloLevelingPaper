"""실행 잠금 아래 제한을 예약하며 transport 재생성으로 초기화되지 않는지 확인한다."""
# ruff: noqa: F811
import os
from concurrent.futures import ThreadPoolExecutor
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores  # noqa: F401
from tests.integration.test_persisted_dispatch_postgres import fixture,SETTINGS,PAYLOAD
from solo_leveling.infrastructure.generation.execution_budget import ExecutionBudget
from solo_leveling.application.provider_dispatch import DispatchBudgetExceeded, DispatchError
pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='실제 PostgreSQL DSN 필요')


@pytest.mark.parametrize('budget', [ExecutionBudget(1,1000,1000,3600),ExecutionBudget(10,150,1000,3600),ExecutionBudget(10,1000,30,3600)])
def test_recreated_transport_does_not_reset_budget(db,stores,pool,budget):
    intent,post,build=fixture(db,stores,pool)
    build('first',budget).send(SETTINGS,PAYLOAD,'generation')
    with pytest.raises(DispatchBudgetExceeded,match='DISPATCH_CALL_TOKEN_BUDGET_EXCEEDED'):
        build('second',budget).send(SETTINGS,PAYLOAD,'repair')
    assert post.call_count==1
    assert len(db('SELECT attempt_id FROM server_provider_attempts WHERE execution_id=%s',(intent.execution_id,)))==1


def test_expired_execution_blocks_before_http(db,stores,pool):
    intent,post,build=fixture(db,stores,pool)
    db("UPDATE server_remote_executions SET created_at=clock_timestamp()-interval '2 hours' WHERE execution_id=%s",(intent.execution_id,))
    with pytest.raises(DispatchBudgetExceeded,match='DISPATCH_TIME_BUDGET_EXCEEDED'):
        build('expired',ExecutionBudget(10,1000,1000,60)).send(SETTINGS,PAYLOAD,'generation')
    post.assert_not_called()


def test_concurrent_keys_share_one_call_slot(db,stores,pool):
    intent,post,build=fixture(db,stores,pool)
    budget=ExecutionBudget(1,1000,1000,3600)
    def send(key):
        try:
            build(key,budget).send(SETTINGS,PAYLOAD,'generation')
            return True
        except DispatchError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(send,['one','two']))==[False,True]
    assert post.call_count==1
