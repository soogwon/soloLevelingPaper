"""C 복구 모델과 실제 영속 조회 연결. 키 보관·인증·생성은 대역이며 호스트 검증이 아니다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores, new_id  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from tests.unit.mcp.test_remote_recovery import Keys
from solo_leveling.application.remote_replay import ExecutionLookup
from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore
from solo_leveling.interfaces.mcp.remote_recovery import AccountRecovery

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


def fixture(db, stores, pool):
    intent, prepared = setup_answer(db, stores, pool)
    payload = PostgresRemoteAnswerStore(pool).save(intent, prepared)
    lookup = ExecutionLookup('alice', 'ask', intent.execution_id, 'input', prepared.binding.context_id)
    keys = Keys()
    keys.put(lookup)
    return intent, prepared, payload, lookup, keys


def snapshot(db):
    # 읽기 복구가 기존 실행/근거/예약·사용량/동의 행을 변경하지 않는지도 비교한다.
    tables = ('server_remote_executions', 'server_processing_evidences', 'server_provider_attempts',
              'server_dispatch_budget_limits', 'server_remote_consents')
    return {table: db(f'SELECT row_to_json(t)::text AS value FROM {table} t ORDER BY 1') for table in tables}


def presenter(keys, owner='alice'):
    model = AccountRecovery(keys)
    model.select_account(owner)
    return model


def test_new_presenter_and_parallel_queries_restore_exact_saved_result(db, stores, pool):
    intent, _, payload, lookup, keys = fixture(db, stores, pool)
    before = snapshot(db)

    def recover(_):
        # 저장소/화면 객체를 매번 재구성한다. 프로세스나 Desktop 재시작 증명은 아니다.
        return presenter(keys).recover('ask', lookup.request_key, PostgresRemoteExecutionStore(pool))

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(recover, range(8)))
    for result in results:
        assert result.status == 'restored'
        assert result.result.execution_id == intent.execution_id
        assert result.result.state == 'completed' and result.result.payload == payload
    assert snapshot(db) == before


def test_account_switch_and_foreign_lookup_cannot_reveal_result(db, stores, pool):
    _, _, _, lookup, keys = fixture(db, stores, pool)
    reader = PostgresRemoteExecutionStore(pool)
    model = presenter(keys)
    assert model.recover('ask', lookup.request_key, reader).status == 'restored'
    model.select_account('bob')
    assert model.snapshot().result is None
    # 다른 계정이 같은 키/context를 알고 있어도 서버 owner 범위를 통과하지 못한다.
    keys.put(replace(lookup, owner_user_id='bob'))
    before = snapshot(db)
    assert model.recover('ask', lookup.request_key, reader).status == 'not_found'
    assert reader.lookup('bob', 'ask', lookup.request_key) is None
    model.select_account('alice')
    assert model.recover('ask', lookup.request_key, reader).status == 'restored'
    assert snapshot(db) == before


@pytest.mark.parametrize('failure,expected', [('hash','conflict'), ('context','conflict'),
    ('corrupt','unavailable'), ('deleted','not_found'), ('missing','not_found')])
def test_recovery_failure_clears_old_result_without_resubmission(db, stores, pool, failure, expected):
    intent, prepared, _, lookup, keys = fixture(db, stores, pool)
    reader = PostgresRemoteExecutionStore(pool)
    model = presenter(keys)
    assert model.recover('ask', lookup.request_key, reader).status == 'restored'
    if failure in ('hash', 'context'):
        altered = replace(lookup, **({'input_hash':'changed'} if failure == 'hash' else {'context_id':new_id()}))
        keys.rows[('alice', 'ask', lookup.request_key)] = altered
    elif failure == 'corrupt':
        db("UPDATE server_remote_executions SET payload_json='{}' WHERE execution_id=%s", (intent.execution_id,))
    elif failure == 'deleted':
        PostgresRemoteDeletionStore(pool).request('alice', prepared.binding.paper_id, new_id())
    else:
        lookup = replace(lookup, request_key=new_id())
        keys.put(lookup)
    before = snapshot(db)
    result = model.recover('ask', lookup.request_key, reader)
    assert result.status == expected and result.result is None
    assert snapshot(db) == before
