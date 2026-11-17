"""실제 PostgreSQL 예약 예산과 worker 종료를 확인한다. 외부 API는 사용하지 않는다."""
# ruff: noqa: F811
import os
import time
from threading import Event
from unittest.mock import Mock
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores, new_id  # noqa: F401
from tests.integration.test_question_admission_postgres import configured, limits
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore, RemoteExecutionLeaseLost
from solo_leveling.workers.remote_question import QuestionMaintenanceRunner, RemoteQuestionRunner, run_question_queue

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


@pytest.fixture(autouse=True)
def isolated(db, monkeypatch):
    monkeypatch.setattr('requests.post', Mock(side_effect=AssertionError('외부 호출 금지')))
    db("UPDATE server_remote_executions SET state='failed',finished_at=clock_timestamp(),lease_token=NULL,lease_expires_at=NULL WHERE question_snapshot IS NOT NULL AND state IN ('queued','running')")


@pytest.mark.parametrize('running', [False, True])
@pytest.mark.parametrize('reason', ['expired', 'storage', 'ai_provider', 'deleted'])
def test_retire_returns_unused_budget_and_rejects_late_completion(db, stores, pool, running, reason):
    submit, policy = configured(db, stores, pool, timeout_seconds=1 if reason == 'expired' else None)
    saved = submit(new_id())
    store = PostgresRemoteExecutionStore(pool)
    intent = store.claim('alice', saved.execution_id) if running else None
    if reason == 'expired':
        time.sleep(1.05)
    elif reason == 'deleted':
        db("UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=(SELECT paper_id FROM server_execution_contexts WHERE context_id=%s)", (saved.lookup.context_id,))
    else:
        db("UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE input_hash=%s AND purpose=%s", (saved.lookup.input_hash, reason))
    execute = Mock(side_effect=AssertionError('무효 작업 실행 금지'))
    result = run_question_queue(RemoteQuestionRunner(store, execute), Event(), max_jobs=1)
    assert result.processed == 1 and result.status == 'limit_reached'
    execute.assert_not_called()
    state = store.lookup('alice', 'ask_paper', saved.lookup.request_key)
    assert state.state == 'failed'
    assert state.terminal_reason == ('deadline_exceeded' if reason == 'expired' else 'scope_revoked')
    assert all(r['reserved_input_tokens'] == r['reserved_output_tokens'] == 0 for r in limits(db, policy))
    assert QuestionMaintenanceRunner(store).run_once() is None
    if intent:
        assert not store.heartbeat(intent) and not store.fail(intent, 'internal_failed')
        with pytest.raises(RemoteExecutionLeaseLost):
            store.complete('alice', saved.execution_id, intent.lease_token, {'context_id': saved.lookup.context_id})


def test_maintenance_does_not_claim_valid_or_active_work(db, stores, pool):
    submit, policy = configured(db, stores, pool)
    saved = submit(new_id()); store = PostgresRemoteExecutionStore(pool)
    maintenance = QuestionMaintenanceRunner(store)
    assert maintenance.run_once() is None
    assert store.lookup('alice', 'ask_paper', saved.lookup.request_key).state == 'queued'
    intent = store.claim('alice', saved.execution_id)
    assert maintenance.run_once() is None and store.heartbeat(intent)
    assert all(r['reserved_input_tokens'] == 1000 for r in limits(db, policy))


@pytest.mark.parametrize('uncertain', [False, True, 'interrupted'])
def test_retirement_keeps_dispatched_allocation_and_never_resends(db, stores, pool, uncertain):
    from types import SimpleNamespace as NS
    from solo_leveling.infrastructure.generation.persisted_dispatch import create_persisted_responses_transport
    from solo_leveling.infrastructure.generation.gated_responses import PreparedProviderAttempt
    from solo_leveling.application.provider_dispatch import DispatchError
    submit, policy = configured(db, stores, pool)
    saved = submit(new_id()); store = PostgresRemoteExecutionStore(pool)
    intent = store.claim('alice', saved.execution_id)
    post = Mock(side_effect=(KeyboardInterrupt() if uncertain == 'interrupted' else TimeoutError('합성 오류')) if uncertain else None,
        return_value=NS(status_code=200, json=lambda: {'usage': {'input_tokens':20,'output_tokens':10}}, close=Mock()))
    transport = create_persisted_responses_transport(pool,intent,policy.version,'test',
        lambda stage,payload,digest: PreparedProviderAttempt('one',digest,100),post=post)
    settings = NS(allow_external_api=True,local_only=False,model='synthetic',max_output_tokens=100,api_key='synthetic',timeout_seconds=5)
    payload = {'model':'synthetic','max_output_tokens':100,'store':False,'input':'합성'}
    if uncertain:
        with pytest.raises(KeyboardInterrupt if uncertain == 'interrupted' else DispatchError):
            transport.send(settings,payload,'generation')
    else:
        transport.send(settings,payload,'generation')
    db("UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s", (intent.ai_consent_id,))
    assert QuestionMaintenanceRunner(store).run_once() == 'blocked'
    for row in limits(db, policy):
        assert row['reserved_input_tokens'] == row['reserved_output_tokens'] == (100 if uncertain else 0)
        assert row['consumed_input_tokens'] == (0 if uncertain else 20)
        assert row['consumed_output_tokens'] == (0 if uncertain else 10)
    assert store.claim_next_question() is None and post.call_count == 1
    assert not store.fail(intent,'internal_failed')
    assert store.lookup('alice','ask_paper',saved.lookup.request_key).usage_state == ('unknown' if uncertain else 'known')


def test_restricted_maintenance_returns_budget_without_provider_permissions(db, stores, pool, monkeypatch):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from solo_leveling.interfaces.cli.question_permissions import grant_question_maintenance_role
    submit, policy = configured(db, stores, pool); saved = submit(new_id())
    db("UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE input_hash=%s", (saved.lookup.input_hash,))
    role = 'question_maintenance_' + new_id().replace('-','')[:12]
    schema = db('SELECT current_schema() AS name')[0]['name']
    with pool.connection() as conn:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-question-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
        grant_question_maintenance_role(conn,schema,role)
    limited = ConnectionPool(make_conninfo(os.environ['SOLO_TEST_POSTGRES_DSN'],user=role,password='local-question-only'),
        kwargs={'row_factory':dict_row,'options':f'-c search_path={schema}'},open=True)
    try:
        from solo_leveling.interfaces.cli.question_worker import main
        monkeypatch.setenv('QUESTION_WORKER_DATABASE_URL', make_conninfo(os.environ['SOLO_TEST_POSTGRES_DSN'],user=role,password='local-question-only'))
        monkeypatch.setenv('QUESTION_WORKER_SCHEMA', schema)
        assert main(['--execute','--runtime','solo_leveling.workers.question_runtime:maintenance_factory','--max-jobs','1']) == 0
        assert all(r['reserved_input_tokens']==r['reserved_output_tokens']==0 for r in limits(db,policy))
        for statement in ('SELECT * FROM server_processing_chunks','SELECT * FROM server_provider_attempts',
                          'UPDATE server_remote_consents SET revoked_at=NULL', 'DELETE FROM server_remote_executions'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with limited.connection() as conn: conn.execute(statement)
    finally:
        limited.close()
        with pool.connection() as conn:
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_concurrent_retirement_releases_reservation_once(db, stores, pool):
    from concurrent.futures import ThreadPoolExecutor
    submit, policy = configured(db, stores, pool); saved = submit(new_id())
    db("UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE input_hash=%s", (saved.lookup.input_hash,))
    with ThreadPoolExecutor(max_workers=4) as workers:
        results=list(workers.map(lambda _: PostgresRemoteExecutionStore(pool).retire_invalid_question(),range(4)))
    assert results.count('blocked') == 1 and results.count(None) == 3
    assert all(r['reserved_input_tokens']==r['reserved_output_tokens']==0 for r in limits(db,policy))
