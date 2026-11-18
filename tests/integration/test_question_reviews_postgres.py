"""검토 준비는 무과금이며 웹 동의 뒤 동일 요청만 접수한다."""
# ruff: noqa: F811
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores,new_id  # noqa: F401
from tests.integration.test_remote_question_input_postgres import setup
from solo_leveling.interfaces.http.remote_question_reviews import PostgresQuestionReviews,NOTICE
from solo_leveling.interfaces.http.remote_executions import RemoteExecutionConflict

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


def review(db,stores,pool):
    request,_,_,_,policy=setup(db,stores,pool)
    version=new_id();policy=replace(policy,version=version)
    binding,question,filters,top,config=request.unpack();config['budget_policy_version']=version
    service=PostgresQuestionReviews(pool,policy=policy,processing_config=config,window_key='test',review_seconds=60,web_origin='https://service.example')
    db("UPDATE server_dispatch_controls SET intake_enabled=true")
    for scope,owner in [('owner','alice'),('service','service')]:
        db("INSERT INTO server_dispatch_budget_limits(scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens) VALUES (%s,%s,%s,'test',1000,1000)",(scope,owner,version))
    key=new_id();service.prepare('alice',key,binding.context_id,question)
    return service,key,binding


def test_prepare_requires_separate_consents_and_replays_same_execution(db,stores,pool):
    service,key,binding=review(db,stores,pool)
    first=service.view('alice',key)
    assert first['storage']==first['ai_provider']=='missing' and first['state'] is None
    assert service.view('bob',key) is None
    with pytest.raises(ValueError):service.submit('alice',key)
    service.grant('alice',key,'storage',NOTICE)
    with pytest.raises(ValueError):service.submit('alice',key)
    with ThreadPoolExecutor(max_workers=3) as workers:
        list(workers.map(lambda _:service.grant('alice',key,'ai_provider',NOTICE),range(3)))
    with ThreadPoolExecutor(max_workers=3) as workers:results=list(workers.map(lambda _:service.submit('alice',key),range(3)))
    assert len({r['execution_id'] for r in results})==1
    assert service.view('alice',key)['execution_id']==results[0]['execution_id']
    assert len(db('SELECT * FROM server_question_budget_reservations WHERE execution_id=%s',(results[0]['execution_id'],)))==1
    assert not db('SELECT * FROM server_provider_attempts WHERE execution_id=%s',(results[0]['execution_id'],))
    assert service.prepare('alice',key,binding.context_id,'합성 질문')['status']=='review_required'
    with pytest.raises(RemoteExecutionConflict):service.prepare('alice',key,binding.context_id,'다른 질문')


def test_revocation_notice_and_expiry_never_admit(db,stores,pool):
    service,key,_=review(db,stores,pool)
    with pytest.raises(ValueError):service.grant('alice',key,'storage','old')
    service.grant('alice',key,'storage',NOTICE);service.grant('alice',key,'ai_provider',NOTICE)
    service.revoke('alice',key,'ai_provider')
    assert service.grant('alice',key,'ai_provider',NOTICE)['ai_provider']=='revoked'
    with pytest.raises(ValueError):service.submit('alice',key)
    assert not db("SELECT execution_id FROM server_remote_executions WHERE request_key=%s",(key,))
    db("UPDATE server_question_reviews SET created_at=clock_timestamp()-interval '2 minutes',expires_at=clock_timestamp()-interval '1 minute' WHERE request_key=%s",(key,))
    assert not service.view('alice',key)['active']
    with pytest.raises(ValueError):service.grant('alice',key,'storage',NOTICE)


def test_mcp_requires_write_scope_and_never_grants_consent(db,stores,pool):
    from fastapi.testclient import TestClient
    from tests.integration.test_remote_product_queries_postgres import Verifier,result
    from tests.integration.test_remote_mcp_product_boundary import SETTINGS,call
    from solo_leveling.application.evidence_qa.remote_contract import VerifiedRemoteIdentity
    from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries
    from solo_leveling.interfaces.http.app import create_app
    service,key,binding=review(db,stores,pool)
    queries=RemoteProductQueries(pool)
    queries.owners.link_identity('alice',VerifiedRemoteIdentity(SETTINGS.issuer,'account-a'))
    class Scoped(Verifier):
        write=False
        async def verify_token(self,token):
            value=await super().verify_token(token)
            if value is not None and self.write:value.scopes.append('questions:write')
            return value
    verifier=Scoped()
    app=create_app(remote_mcp_settings=replace(SETTINGS,product_reads_enabled=True),remote_mcp_verifier=verifier,
        remote_product_queries=queries,remote_questions=service)
    with TestClient(app,base_url='https://service.example') as client:
        assert call(client,'tools/call',{'name':'submit_remote_question','arguments':{'request_key':key}}).status_code==403
        verifier.write=True
        assert result(client,'submit_remote_question',{'request_key':key})['status']=='review_required'
        assert service.view('alice',key)['ai_provider']=='missing'
        service.grant('alice',key,'storage',NOTICE);service.grant('alice',key,'ai_provider',NOTICE)
        submitted=result(client,'submit_remote_question',{'request_key':key})
        assert submitted['state']=='queued'
        assert result(client,'submit_remote_question',{'request_key':key})['execution_id']==submitted['execution_id']
        assert result(client,'get_remote_question',{'request_key':key})['status']=='ok'


def test_restricted_review_role_submits_without_web_secrets_or_policy_edits(db,stores,pool):
    from psycopg import sql
    import psycopg
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from tests.integration.test_remote_processing_postgres import DSN
    from solo_leveling.interfaces.cli.question_permissions import grant_question_review_role
    service,key,_=review(db,stores,pool)
    role='question_'+new_id().replace('-','')[:12]
    with pool.connection() as conn:
        schema=conn.execute('SELECT current_schema() AS s').fetchone()['s']
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-question-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
        grant_question_review_role(conn,schema,role)
    restricted=ConnectionPool(make_conninfo(DSN,user=role,password='local-question-only'),kwargs={'row_factory':dict_row,'options':f'-c search_path={schema}'},open=True)
    try:
        limited=PostgresQuestionReviews(restricted,policy=service.policy,processing_config=service.config,window_key=service.window,review_seconds=service.seconds,web_origin=service.web_origin)
        limited.grant('alice',key,'storage',NOTICE);limited.grant('alice',key,'ai_provider',NOTICE)
        saved=limited.submit('alice',key)
        assert saved['state']=='queued' and limited.submit('alice',key)['execution_id']==saved['execution_id']
        for statement in ('SELECT * FROM web_sessions','SELECT * FROM server_device_credentials','DELETE FROM server_question_reviews',
            'UPDATE server_dispatch_budget_limits SET max_input_tokens=999999','UPDATE server_papers SET paper_id=paper_id',
            "UPDATE server_remote_executions SET state='completed'",'UPDATE server_dispatch_controls SET control_id=control_id'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with restricted.connection() as conn:conn.execute(statement)
    finally:
        restricted.close()
        with pool.connection() as conn:
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_count_notice_is_pinned_and_legacy_consent_cannot_admit(db,stores,pool):
    from solo_leveling.interfaces.http.remote_question_reviews import COUNT_NOTICE
    service,old,binding=review(db,stores,pool)
    service.grant('alice',old,'storage',NOTICE)
    service.grant('alice',old,'ai_provider',NOTICE)
    config={**service.config,'provider_profile_id':'general-question-'+'a'*64,'semantic_model':'disabled'}
    general=PostgresQuestionReviews(pool,policy=replace(service.policy,max_calls=1),processing_config=config,
        window_key=service.window,review_seconds=60,web_origin=service.web_origin,notice_version=COUNT_NOTICE)
    assert general.view('alice',old)['transfer']['total_max_calls']==service.policy.max_calls
    assert not general.view('alice',old)['active']
    with pytest.raises(ValueError):general.submit('alice',old)
    key=new_id();general.prepare('alice',key,binding.context_id,'계수 동의 합성 질문')
    data=general.view('alice',key)
    assert data['notice_version']==COUNT_NOTICE
    assert data['transfer']==dict(input_count_max_calls=1,generation_max_calls=1,total_max_calls=2)
    assert '근거 ID' in data['ai_notice'] and '최대 2회' in data['ai_notice']
    with pytest.raises(ValueError):general.grant('alice',key,'ai_provider',NOTICE)
    general.grant('alice',key,'storage',COUNT_NOTICE);general.grant('alice',key,'ai_provider',COUNT_NOTICE)
    saved=general.submit('alice',key)
    row=db('SELECT notice_version FROM server_remote_executions WHERE execution_id=%s',(saved['execution_id'],))[0]
    assert row['notice_version']==COUNT_NOTICE
    assert general.submit('alice',key)['execution_id']==saved['execution_id']
    assert not db('SELECT * FROM server_question_token_counts WHERE execution_id=%s',(saved['execution_id'],))
    assert not db('SELECT * FROM server_provider_attempts WHERE execution_id=%s',(saved['execution_id'],))
