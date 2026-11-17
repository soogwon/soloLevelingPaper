"""질문 전용 비특권 계정으로 검색·대역 생성·저장·정산과 권한 차단을 검증한다."""
# ruff: noqa: F811
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores, new_id  # noqa: F401
from tests.integration.test_question_composition_postgres import prepare
from solo_leveling.interfaces.cli.question_permissions import grant_question_execution_role
from solo_leveling.workers.question_composition import create_question_runner

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


@pytest.mark.parametrize('fault',[None,'network','revoked'])
def test_restricted_execution_and_denied_operations(db,stores,pool,fault,monkeypatch):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from unittest.mock import Mock
    monkeypatch.setattr('requests.post',Mock(side_effect=AssertionError('실제 API 금지')))
    store,saved,prepared,post=prepare(db,stores,pool,fault=fault,admitted=True)
    source=prepared.execute.execute
    role='question_execute_'+new_id().replace('-','')[:12]
    schema=db('SELECT current_schema() AS name')[0]['name']
    with pool.connection() as conn:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'synthetic-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
        grant_question_execution_role(conn,schema,role)
    limited=ConnectionPool(make_conninfo(os.environ['SOLO_TEST_POSTGRES_DSN'],user=role,password='synthetic-only'),
        kwargs={'row_factory':dict_row,'options':f'-c search_path={schema}'},open=True)
    try:
        runner=create_question_runner(limited,generation=source.generation,semantic=source.semantic,
            provider_profile_id=source.profile,embedding_runtime=source.embedding,window_key=source.window,
            token_upper_bound=source.estimate,post=post)
        assert runner.run_once()==('finished' if fault is None else 'failed')
        row=store.lookup('alice','ask_paper',saved.lookup.request_key)
        assert row.state==('completed' if fault is None else 'failed') and post.call_count==1
        assert runner.run_once() is None
        budget=db('''SELECT b.reserved_input_tokens,b.consumed_input_tokens FROM server_dispatch_budget_limits b
            JOIN server_question_budget_reservations r ON r.policy_version=b.policy_version AND r.window_key=b.window_key
            WHERE r.execution_id=%s''',(saved.execution_id,))
        assert len(budget)==2
        assert all(r['reserved_input_tokens']==(500 if fault=='network' else 0) for r in budget)
        for statement in (
            'UPDATE server_remote_consents SET consent_id=consent_id',
            'UPDATE server_papers SET paper_id=paper_id',
            'UPDATE server_processing_publications SET publication_id=publication_id',
            'UPDATE server_dispatch_controls SET control_id=control_id',
            'UPDATE server_remote_consents SET revoked_at=NULL',
            'UPDATE server_processing_chunks SET original_text=original_text',
            'UPDATE server_dispatch_budget_limits SET max_input_tokens=999999',
            'UPDATE server_remote_executions SET policy_snapshot=policy_snapshot',
            'DELETE FROM server_provider_attempts', 'SELECT * FROM web_sessions',
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with limited.connection() as conn: conn.execute(statement)
    finally:
        limited.close()
        with pool.connection() as conn:
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_policy_scoped_worker_leaves_other_queued_and_revoked_jobs(db,stores,pool):
    from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
    store,old,_,_=prepare(db,stores,pool,admitted=True)
    _,current,_,_=prepare(db,stores,pool,admitted=True)
    policy=store.policy('alice',current.execution_id)
    scoped=PostgresRemoteExecutionStore(pool,question_policy_version=policy.version)
    db("UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE input_hash=%s",(old.lookup.input_hash,))
    assert scoped.retire_invalid_question() is None
    selected=scoped.claim_next_question()
    assert selected[0]=='claimed' and selected[1].execution_id==current.execution_id
    assert store.lookup('alice','ask_paper',old.lookup.request_key).state=='queued'
    assert scoped.fail(selected[1],'internal_failed')
    assert store.retire_invalid_question()=='blocked'


def test_readonly_preview_matches_real_bootstrap_dispatch(db,stores,pool,tmp_path,monkeypatch):
    from dataclasses import asdict
    import json
    from unittest.mock import Mock
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from solo_leveling.workers import question_bootstrap as bootstrap
    from solo_leveling.infrastructure.generation.gated_responses import payload_digest
    from tests.unit.test_question_bootstrap import raw_config
    monkeypatch.setattr('requests.post',Mock(side_effect=AssertionError('실제 API 금지')))
    store,saved,prepared,post=prepare(db,stores,pool,admitted=True)
    source=prepared.execute.execute
    raw=raw_config();raw['policy']=asdict(store.policy('alice',saved.execution_id))
    raw['generation']=dict(model=source.generation.model,timeout_seconds=source.generation.timeout_seconds,max_output_tokens=source.generation.max_output_tokens)
    raw['provider_profile_id']=source.profile;raw['window_key']=source.window
    raw['requests']=dict(max_requests=1,max_input_tokens=500,max_output_tokens=100,payloads={'a'*64:500})
    raw['schema_name']=db('SELECT current_schema() AS name')[0]['name']
    raw['database_name']=db('SELECT current_database() AS name')[0]['name']
    role='question_bootstrap_'+new_id().replace('-','')[:12];raw['database_role']=role
    path=tmp_path/'plan.json';path.write_text(json.dumps(raw));plan=bootstrap.load_plan(path)
    with pytest.raises(ValueError):
        bootstrap.preview_payload(pool,plan,'other-owner',saved.execution_id,source.embedding)
    first=bootstrap.preview_payload(pool,plan,'alice',saved.execution_id,source.embedding)
    second=bootstrap.preview_payload(pool,plan,'alice',saved.execution_id,source.embedding)
    assert first==second and post.call_count==0
    assert store.lookup('alice','ask_paper',saved.lookup.request_key).state=='queued'
    assert not db('SELECT attempt_id FROM server_provider_attempts WHERE execution_id=%s',(saved.execution_id,))
    raw['requests']['payloads']={payload_digest(first):500};path.write_text(json.dumps(raw));plan=bootstrap.load_plan(path)
    with pool.connection() as conn:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'synthetic-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
        grant_question_execution_role(conn,raw['schema_name'],role)
    values=conninfo_to_dict(os.environ['SOLO_TEST_POSTGRES_DSN'])
    dsn=f"postgresql://{role}:synthetic-only@{values['host']}:{values['port']}/{raw['database_name']}"
    try:
        with bootstrap.build(plan,dict(QUESTION_WORKER_EXECUTION_ENABLED='true',QUESTION_WORKER_DATABASE_URL=dsn,
             QUESTION_WORKER_OPENAI_API_KEY='synthetic'),check=lambda _:None,embedding_factory=lambda _:source.embedding,post=post) as runner:
            assert runner.run_once()=='finished'
        assert store.lookup('alice','ask_paper',saved.lookup.request_key).state=='completed'
        assert post.call_count==1 and post.call_args.kwargs['json']==first
    finally:
        with pool.connection() as conn:
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
