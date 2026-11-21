"""별도 비특권 연결로 웹 동의와 MCP 저장 권한을 교차 검증한다."""
# ruff: noqa: F811
import hashlib
import os
from uuid import uuid4
import pytest
from tests.integration.test_remote_processing_postgres import pool,db,stores,DSN  # noqa: F401
from tests.integration.test_remote_reflection_postgres import setup
from solo_leveling.interfaces.cli.reflection_permissions import grant_reflection_role
from solo_leveling.interfaces.http.remote_reflection import PostgresRemoteReflection,NOTICE

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='격리 PostgreSQL 필요')


def test_web_and_mcp_roles_are_separate_and_fail_closed(pool,db,stores):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    _,binding,args=setup(pool,db,stores)
    roles=['reflection_web_'+uuid4().hex[:10],'reflection_mcp_'+uuid4().hex[:10]]
    pools=[]
    with pool.connection() as conn:
        schema=conn.execute('SELECT current_schema() s').fetchone()['s']
        for role,web in zip(roles,(True,False)):
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-reflection-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
            grant_reflection_role(conn,schema,role,web=web)
            grant_reflection_role(conn,schema,role,web=web)
    try:
        for role in roles:
            pools.append(ConnectionPool(make_conninfo(DSN,user=role,password='local-reflection-only'),
                kwargs={'row_factory':dict_row,'options':f'-c search_path={schema}'},open=True))
        web,mcp=map(PostgresRemoteReflection,pools)
        context=binding.context_id
        web.revoke_storage('alice',context)
        grant=web.grant_storage('alice',context,NOTICE,args['question_revision'])
        key=str(uuid4());saved=mcp.save('alice',context,key,**(args|{'storage_consent_id':grant['consent_id']}))
        assert mcp.comparison('alice',context)['context_id']==context
        assert mcp.questions('bob',context) is None
        deleted=mcp.delete_answer('alice',context,args['template_id'],saved['revision'])
        assert deleted['revision']==2
        assert mcp.export('alice',context)['request_history'][0]['state']=='deleted'
        web.revoke_storage('alice',context)
        with pytest.raises(ValueError,match='consent_required'):
            mcp.save('alice',context,str(uuid4()),**(args|{'expected_revision':2,'storage_consent_id':grant['consent_id']}))
        for index,statements in enumerate((
            ["INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version) VALUES (gen_random_uuid(),'alice','ai_provider','other','other')",
             "UPDATE server_remote_consents SET revoked_at=NULL",
             "UPDATE server_reflection_answers SET body='changed'"],
            ["INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version) VALUES (gen_random_uuid(),'alice','storage','other','other')",
             "UPDATE server_remote_consents SET revoked_at=clock_timestamp()",
             "UPDATE server_reflection_requests SET input_hash='changed'",
             "UPDATE server_reflection_contexts SET question_revision='changed'"]
        )):
            for statement in [*statements,'SELECT * FROM web_sessions','SELECT * FROM server_device_credentials',
                              'UPDATE server_papers SET paper_id=paper_id','UPDATE server_dispatch_controls SET dispatch_enabled=true']:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with pools[index].connection() as conn:conn.execute(statement)
        with pytest.raises(ValueError,match='profile_changed'):
            with pool.connection() as conn:grant_reflection_role(conn,schema,roles[0],web=False)
    finally:
        for restricted in pools:restricted.close()
        with pool.connection() as conn:
            for role in roles:
                trigger='reflection_role_'+hashlib.sha256(role.encode()).hexdigest()[:12]
                for table in ('server_papers','server_remote_consents'):
                    conn.execute(sql.SQL('DROP TRIGGER {} ON {}.{}').format(sql.Identifier(trigger),sql.Identifier(schema),sql.Identifier(table)))
                conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_mcp_role_uses_account_consent_without_reading_consent_table(pool,db,stores):
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from solo_leveling.interfaces.http.account_consents import NOTICE_VERSION,PostgresAccountConsents
    _,binding,args=setup(pool,db,stores)
    context=binding.context_id
    db("DELETE FROM server_remote_consents WHERE owner_user_id='alice' AND input_hash=%s",
       (NOTICE+':'+args['question_revision'],))
    roles=['reflection_web_'+uuid4().hex[:10],'reflection_mcp_'+uuid4().hex[:10]]
    pools=[]
    account=PostgresAccountConsents(pool)
    account.grant('alice',['storage'],NOTICE_VERSION)
    with pool.connection() as conn:
        schema=conn.execute('SELECT current_schema() s').fetchone()['s']
        for role,web in zip(roles,(True,False)):
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-reflection-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
            grant_reflection_role(conn,schema,role,web=web)
    try:
        for role in roles:
            pools.append(ConnectionPool(make_conninfo(DSN,user=role,password='local-reflection-only'),
                kwargs={'row_factory':dict_row,'options':f'-c search_path={schema}'},open=True))
        web,mcp=map(PostgresRemoteReflection,pools)
        view=mcp.consent('alice',context)
        assert view['account'] is True
        saved=mcp.save('alice',context,str(uuid4()),**(args|{'storage_consent_id':view['consent_id']}))
        assert saved['revision']==1
        web.revoke_storage('alice',context)
        with pytest.raises(ValueError,match='consent_required'):
            mcp.save('alice',context,str(uuid4()),**(args|{'expected_revision':1,'storage_consent_id':view['consent_id']}))
    finally:
        account.revoke('alice','storage')
        for restricted in pools:restricted.close()
        with pool.connection() as conn:
            for role in roles:
                trigger='reflection_role_'+hashlib.sha256(role.encode()).hexdigest()[:12]
                for table in ('server_papers','server_remote_consents'):
                    conn.execute(sql.SQL('DROP TRIGGER {} ON {}.{}').format(sql.Identifier(trigger),sql.Identifier(schema),sql.Identifier(table)))
                conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
