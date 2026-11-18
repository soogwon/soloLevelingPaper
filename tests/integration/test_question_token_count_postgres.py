"""계수와 생성 모두 합성 HTTP로만 실행하며 재시작·동의·권한 차단을 검사한다."""
# ruff: noqa: F811
import os
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores, new_id  # noqa: F401
from tests.integration.test_question_composition_postgres import prepare
from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.workers.question_token_counter import PersistedTokenCounter, NOTICE

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr('requests.post',Mock(side_effect=AssertionError('실제 외부 호출 금지')))


def setup(db,stores,pool,notice=True,claim=True):
    from solo_leveling.workers import question_bootstrap as b
    store,saved,prepared,generation=prepare(db,stores,pool,admitted=True,general=True,counting=True)
    source=prepared.execute.execute
    if notice:
        db('UPDATE server_remote_consents SET notice_version=%s WHERE input_hash=%s',(NOTICE,saved.lookup.input_hash))
        db('UPDATE server_remote_executions SET notice_version=%s WHERE execution_id=%s',(NOTICE,saved.execution_id))
    raw=dict(generation=dict(model='synthetic',timeout_seconds=5,max_output_tokens=100),
             provider_profile_id=source.profile,window_key=source.window)
    p=NS(raw=raw,policy=store.policy('alice',saved.execution_id))
    payload=b.preview_payload(pool,p,'alice',saved.execution_id,source.embedding)
    intent=store.claim('alice',saved.execution_id) if claim else None
    return store,saved,source,generation,payload,intent


def counter(pool,source,intent,post,**kwargs):
    return PersistedTokenCounter(pool,intent,source.contract,lambda:True,
                                 api_key='synthetic',enabled=True,timeout_seconds=5,post=post,**kwargs)


def response(count=500):
    return NS(status_code=200,json=lambda:dict(object='response.input_tokens',input_tokens=count),close=Mock())


def test_exact_count_reused_without_second_request(db,stores,pool):
    store,saved,source,gen,payload,intent=setup(db,stores,pool)
    http=Mock(return_value=response())
    assert counter(pool,source,intent,http)('synthetic',payload)==500
    assert counter(pool,source,intent,http)('synthetic',payload)==500
    assert http.call_count==1 and gen.call_count==0
    assert set(http.call_args.kwargs['json'])=={'model','instructions','input','text'}
    assert http.call_args.kwargs['allow_redirects'] is False
    changed=dict(payload,instructions='changed')
    with pytest.raises(DispatchError):counter(pool,source,intent,http)('synthetic',changed)
    assert http.call_count==1


@pytest.mark.parametrize('failure',['timeout','invalid','over_limit','old_notice','revoked','stale','budget'])
def test_rejected_or_uncertain_count_never_retries(db,stores,pool,failure):
    store,saved,source,gen,payload,intent=setup(db,stores,pool,notice=failure!='old_notice')
    http=Mock(return_value=response(2001 if failure=='over_limit' else True if failure=='invalid' else 500))
    if failure=='timeout':http.side_effect=TimeoutError('비공개 오류')
    if failure=='revoked':db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s',(intent.ai_consent_id,))
    if failure=='stale':db("UPDATE server_remote_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=%s",(saved.execution_id,))
    if failure=='budget':db('UPDATE server_dispatch_budget_limits SET max_input_tokens=max_input_tokens+1 WHERE policy_version=%s',(source.contract.policy_version,))
    for _ in range(2):
        with pytest.raises(DispatchError):counter(pool,source,intent,http)('synthetic',payload)
    assert http.call_count==(1 if failure in ('timeout','invalid','over_limit') else 0)
    gen.assert_not_called()


def test_revocation_during_count_does_not_authorize_generation(db,stores,pool):
    store,saved,source,gen,payload,intent=setup(db,stores,pool)
    def post(*args,**kw):
        db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s',(intent.ai_consent_id,))
        return response()
    with pytest.raises(DispatchError):counter(pool,source,intent,post)('synthetic',payload)
    assert db('SELECT status FROM server_question_token_counts WHERE execution_id=%s',(saved.execution_id,))==[{'status':'unknown'}]
    gen.assert_not_called()


def test_general_bootstrap_restricted_role_count_then_generate(db,stores,pool,tmp_path):
    from dataclasses import asdict
    import json
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from tests.unit.test_question_bootstrap import raw_config
    from solo_leveling.workers import question_bootstrap as b
    from solo_leveling.interfaces.cli.question_permissions import grant_question_execution_role, grant_question_token_count_role
    store,saved,source,generation,payload,_=setup(db,stores,pool,claim=False)
    schema=db('SELECT current_schema() AS name')[0]['name']
    database=db('SELECT current_database() AS name')[0]['name']
    role='count_test_'+new_id().replace('-','')[:12]
    raw=raw_config();raw.pop('requests')
    raw.update(schema_version=2,database_name=database,schema_name=schema,database_role=role,
        policy=asdict(store.policy('alice',saved.execution_id)),provider_profile_id=source.profile,
        window_key=source.window,contract=asdict(source.contract),counting={'timeout_seconds':5},
        generation=dict(model='synthetic',timeout_seconds=5,max_output_tokens=100))
    path=tmp_path/'plan.json';path.write_text(json.dumps(raw));plan=b.load_plan(path)
    with pool.connection() as conn:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'synthetic-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
        grant_question_execution_role(conn,schema,role)
        grant_question_token_count_role(conn,schema,role)
    values=conninfo_to_dict(os.environ['SOLO_TEST_POSTGRES_DSN'])
    dsn=f"postgresql://{role}:synthetic-only@{values['host']}:{values['port']}/{database}"
    http=Mock(return_value=response())
    try:
        with b.build(plan,dict(QUESTION_WORKER_EXECUTION_ENABLED='true',QUESTION_WORKER_TOKEN_COUNT_ENABLED='true',
            QUESTION_WORKER_DATABASE_URL=dsn,QUESTION_WORKER_OPENAI_API_KEY='synthetic'),check=lambda _:None,
            embedding_factory=lambda _:source.embedding,post=generation,token_count_post=http) as runner:
            assert runner.run('alice',saved.execution_id)=='finished'
            assert runner.run('alice',saved.execution_id)=='not_claimed'
        assert http.call_count==generation.call_count==1
        actual=generation.call_args.kwargs['json']
        assert http.call_args.kwargs['json']=={k:actual[k] for k in ('model','instructions','input','text')}
        assert store.lookup('alice','ask_paper',saved.lookup.request_key).state=='completed'
        assert db('SELECT status,input_tokens FROM server_question_token_counts WHERE execution_id=%s',(saved.execution_id,))==[dict(status='observed',input_tokens=500)]
        for statement in ('DELETE FROM server_question_token_counts',
                          "UPDATE server_question_token_counts SET profile='other'",
                          'UPDATE server_dispatch_budget_limits SET max_input_tokens=999999',
                          'UPDATE server_remote_consents SET revoked_at=NULL'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with psycopg.connect(dsn,options=f'-c search_path={schema}') as conn:conn.execute(statement)
    finally:
        with pool.connection() as conn:
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_concurrent_count_reservation_sends_at_most_once(db,stores,pool):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    _,saved,source,gen,payload,intent=setup(db,stores,pool)
    entered,release=Event(),Event()
    def post(*args,**kw):
        entered.set(); assert release.wait(5)
        return response()
    http=Mock(side_effect=post)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(counter(pool,source,intent,http),'synthetic',payload)
        try:
            assert entered.wait(5)
            with pytest.raises(DispatchError):counter(pool,source,intent,http)('synthetic',payload)
        finally:release.set()
        assert future.result()==500
    assert http.call_count==1
    gen.assert_not_called()


def test_execution_delete_cascades_count_metadata(db,stores,pool):
    _,saved,source,_,payload,intent=setup(db,stores,pool)
    assert counter(pool,source,intent,Mock(return_value=response()))('synthetic',payload)==500
    db('DELETE FROM server_remote_executions WHERE execution_id=%s',(saved.execution_id,))
    assert not db('SELECT execution_id FROM server_question_token_counts WHERE execution_id=%s',(saved.execution_id,))


@pytest.mark.parametrize('observed', [True, False])
def test_new_lease_reuses_only_observed_count(db,stores,pool,observed):
    store,saved,source,_,payload,intent=setup(db,stores,pool)
    http=Mock(return_value=response(),side_effect=None if observed else TimeoutError('합성 오류'))
    if observed:
        assert counter(pool,source,intent,http)('synthetic',payload)==500
    else:
        with pytest.raises(DispatchError):counter(pool,source,intent,http)('synthetic',payload)
    db("UPDATE server_remote_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=%s",(saved.execution_id,))
    fresh=store.claim('alice',saved.execution_id)
    assert fresh.lease_token!=intent.lease_token
    if observed:
        assert counter(pool,source,fresh,http)('synthetic',payload)==500
    else:
        with pytest.raises(DispatchError):counter(pool,source,fresh,http)('synthetic',payload)
    assert http.call_count==1
