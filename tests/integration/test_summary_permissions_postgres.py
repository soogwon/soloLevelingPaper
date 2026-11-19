"""로컬 제한 계정의 동의·접수·대역 생성·조회와 금지 권한을 검증한다."""
# ruff: noqa: F811
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores, DSN  # noqa: F401
from tests.integration.test_remote_summary_intake_postgres import setup
from solo_leveling.interfaces.cli.summary_permissions import grant_summary_role
from solo_leveling.interfaces.http.remote_summary_reviews import PostgresSummaryReviews
from solo_leveling.workers.remote_summary import PersistedSummaryExecutor, RemoteSummaryRunner
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


@pytest.fixture(scope='module')
def restricted(pool):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    runtimes = []
    roles = []
    try:
        for worker in (False, True):
            role = 'summary_test_' + uuid4().hex[:12]
            with pool.connection() as conn:
                schema = conn.execute('SELECT current_schema() AS s').fetchone()['s']
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-test-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
                grant_summary_role(conn,schema,role,worker=worker)
                grant_summary_role(conn,schema,role,worker=worker)
            roles.append(role)
            runtimes.append(ConnectionPool(make_conninfo(DSN,user=role,password='local-test-only'),
                kwargs=dict(row_factory=dict_row,options=f'-c search_path={schema}'),min_size=1,max_size=4))
        yield runtimes, roles, schema
    finally:
        for runtime in runtimes:
            runtime.close()
        with psycopg.connect(DSN) as conn:
            for role in roles:
                conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def review(db,stores,pool,runtime):
    request,_,_,policy = setup(db,stores,pool)
    config = request.unpack()[2]
    config = {k:config[k] for k in ('provider','model','prompt_version','language','budget_policy_version')}
    config.update(provider="openai",prompt_version="remote-summary-v1")
    from solo_leveling.interfaces.http.remote_summary_sources import PostgresSummaryPreparation
    request = PostgresSummaryPreparation(pool,**config).prepare("alice",request.unpack()[0].context_id)
    service = PostgresSummaryReviews(runtime,settings=config,policy=policy,window_key='test',review_seconds=60,web_origin='https://example.test')
    key, context = str(uuid4()),request.unpack()[0].context_id
    assert service.prepare('alice',key,context)['status']=='review_required'
    for purpose in ('storage','ai_provider'):
        assert service.grant('alice',key,purpose,'remote-summary-v1')[purpose]=='current'
    return service,key,context,request,policy


def test_restricted_end_to_end_and_restore(pool,db,stores,restricted):
    runtimes,_,_ = restricted
    service,key,context,request,policy = review(db,stores,pool,runtimes[0])
    saved = service.submit('alice',key)
    assert service.submit('alice',key)==saved
    evidence = request.unpack()[1][0].evidence_id
    content = json.dumps({'items':[dict(kind='method',text='합성 요약',evidence_ids=[evidence])]})
    envelope = dict(status='completed',output=[dict(type='message',role='assistant',status='completed',
        content=[dict(type='output_text',text=content)])],usage=dict(input_tokens=20,output_tokens=10))
    post = Mock(return_value=SimpleNamespace(status_code=200,json=lambda:envelope,close=Mock()))
    settings = OpenAIGenerationSettings('synthetic',model='fixed',max_output_tokens=100,allow_external_api=True)
    executor = PersistedSummaryExecutor(runtimes[1],settings=settings,token_upper_bound=lambda *_:100,window_key='test',post=post)
    runner = RemoteSummaryRunner(runtimes[1],executor,policy_version=policy.version,window_key="test")
    assert runner.run_once()=='finished'
    assert runner.run('alice',saved['execution_id'])=='not_claimed'
    assert post.call_count==1
    assert service.result('alice',key)['result']['items'][0]['text']=='합성 요약'
    assert service.latest('alice',context)['request_key']==key
    assert service.evidence('alice',key,evidence)['original_text']==request.unpack()[1][0].original_text
    assert service.result('bob',key) is None
    assert service.latest('bob',context) is None
    limits = db('SELECT reserved_input_tokens,consumed_input_tokens FROM server_dispatch_budget_limits WHERE policy_version=%s',(policy.version,))
    assert all(row==dict(reserved_input_tokens=0,consumed_input_tokens=20) for row in limits)


@pytest.mark.parametrize('mode',['cancel','revoke'])
def test_restricted_stop_returns_budget_without_sending(pool,db,stores,restricted,mode):
    runtimes,_,_ = restricted
    service,key,_,_,policy = review(db,stores,pool,runtimes[0])
    saved = service.submit('alice',key)
    if mode=='cancel':
        assert service.cancel('alice',key)['state']=='cancel_requested'
    else:
        assert service.revoke('alice',key,'ai_provider')['ai_provider']=='revoked'
    post = Mock()
    settings = OpenAIGenerationSettings('synthetic',model='fixed',max_output_tokens=100,allow_external_api=True)
    executor = PersistedSummaryExecutor(runtimes[1],settings=settings,token_upper_bound=lambda *_:100,window_key='test',post=post)
    assert RemoteSummaryRunner(runtimes[1],executor).run('alice',saved['execution_id'])==('cancelled' if mode=='cancel' else 'blocked')
    post.assert_not_called()
    assert all(row['reserved_input_tokens']==0 for row in db('SELECT reserved_input_tokens FROM server_dispatch_budget_limits WHERE policy_version=%s',(policy.version,)))


def test_forbidden_mutations_and_role_reuse(pool,db,stores,restricted):
    import psycopg
    runtimes,roles,schema = restricted
    service,key,_,_,_ = review(db,stores,pool,runtimes[0])
    service.submit('alice',key)
    for index,runtime in enumerate(runtimes):
        statements = ['SELECT * FROM web_sessions','SELECT * FROM server_device_credentials',
            'CREATE TABLE forbidden(id int)','DELETE FROM server_remote_executions',
            'UPDATE server_dispatch_budget_limits SET max_input_tokens=999999',
            'UPDATE server_processing_chunks SET original_text=original_text',
            'UPDATE server_papers SET paper_id=paper_id',
            'UPDATE server_dispatch_controls SET control_id=control_id']
        statements += (['UPDATE server_remote_consents SET revoked_at=clock_timestamp()'] if index else [
            "UPDATE server_remote_executions SET state='running'",
            'UPDATE server_provider_attempts SET status=status'])
        with runtime.connection() as conn:
            for statement in statements:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with conn.transaction():
                        conn.execute(statement)
        with pool.connection() as conn:
            with pytest.raises(ValueError,match='summary_role_profile_changed'):
                grant_summary_role(conn,schema,roles[index],worker=not bool(index))


def test_other_operation_and_consent_are_not_mutable(pool,db,stores,restricted):
    import psycopg
    from tests.integration.test_question_admission_postgres import configured
    submit,_ = configured(db,stores,pool)
    question = submit(str(uuid4()))
    assert question is not None
    runtimes,_,_ = restricted
    for runtime in runtimes:
        with runtime.connection() as conn:
            for statement in (
                'UPDATE server_remote_executions SET updated_at=clock_timestamp() WHERE execution_id=%s',
                'UPDATE server_question_budget_reservations SET remaining_input_tokens=0 WHERE execution_id=%s',
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege,match='summary_scope_invalid'):
                    with conn.transaction():
                        conn.execute(statement,(question.execution_id,))
    with runtimes[0].connection() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege,match='summary_scope_invalid'):
            with conn.transaction():
                conn.execute("UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE notice_version='notice'")
    assert db('SELECT state FROM server_remote_executions WHERE execution_id=%s',(question.execution_id,))[0]['state']=='queued'


def test_privileged_role_is_rejected_before_grants(pool):
    with pool.connection() as conn:
        role = conn.execute('SELECT current_user AS r').fetchone()['r']
        schema = conn.execute('SELECT current_schema() AS s').fetchone()['s']
        with pytest.raises(ValueError,match='summary_role_not_isolated'):
            grant_summary_role(conn,schema,role)
