"""실제 MCP 인증·취소 역할·연결 변경·응답 복구를 합성 PostgreSQL로 검증한다."""
# ruff: noqa: F811
import os
from dataclasses import replace
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_remote_processing_postgres import pool,db,stores,make_paper,HASH  # noqa: F401
from tests.integration.test_remote_product_queries_postgres import Verifier,result
from tests.integration.test_remote_mcp_product_boundary import SETTINGS,call
from solo_leveling.application.evidence_qa.remote_contract import VerifiedRemoteIdentity
from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.processing_cancellations import PostgresProcessingCancellationStore
from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='로컬 PostgreSQL 필요')


class CancelVerifier(Verifier):
    async def verify_token(self, token):
        value=await super().verify_token('account-a' if token=='rotated' else token)
        if value:
            value.token=token
            if token!='account-b': value.scopes.append('jobs:cancel')
        return value


def test_scope_confirmation_token_rotation_and_recovery(db,stores,pool):
    owner='cancel-mcp-'+uuid4().hex
    paper=make_paper(db,owner)
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'합성')",(owner,))
    q=RemoteProductQueries(pool)
    q.owners.link_identity(owner,VerifiedRemoteIdentity(SETTINGS.issuer,'account-a'))
    other='other-'+uuid4().hex
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'다른 합성 계정')",(other,))
    q.owners.link_identity(other,VerifiedRemoteIdentity(SETTINGS.issuer,'account-b'))
    job=stores[0].enqueue(owner,paper,'v1','parse',str(uuid4()),HASH)
    service=PostgresProcessingCancellationStore(pool)
    app=create_app(remote_mcp_settings=replace(SETTINGS,product_reads_enabled=True),remote_mcp_verifier=CancelVerifier(),remote_product_queries=q,remote_job_cancellations=service)
    with TestClient(app,base_url=SETTINGS.resource_url[:-4]) as client:
        metadata=client.get('/.well-known/oauth-protected-resource/mcp').json()
        assert 'jobs:cancel' in metadata['scopes_supported']
        assert call(client,'tools/list',token='account-b').status_code==200
        args={'job_id':job.job_id,'request_key':str(uuid4())}
        assert result(client,'prepare_remote_job_cancellation',args,token='account-b')['status']=='not_authorized'
        preview=result(client,'prepare_remote_job_cancellation',args)['result']
        assert preview['can_cancel'] and preview['expected_revision']==1
        assert not db('SELECT * FROM server_processing_cancel_requests')
        confirmed={**args,'expected_revision':1,'binding':preview['binding'],'confirmed':False}
        assert result(client,'confirm_remote_job_cancellation',confirmed)['status']=='invalid_argument'
        confirmed['confirmed']=True
        assert result(client,'confirm_remote_job_cancellation',confirmed,token='rotated')['result']['connection_changed']
        assert not db('SELECT * FROM server_processing_cancel_requests')
        done=result(client,'confirm_remote_job_cancellation',confirmed)
        assert done['result']['status']=='cancelled'
        assert result(client,'confirm_remote_job_cancellation',confirmed)==done
        assert result(client,'get_remote_job_cancellation',args,token='rotated')==done
        assert result(client,'get_remote_job_cancellation',args,token='account-b')['status']=='not_found'
        assert result(client,'prepare_remote_job_cancellation',args)['result']['recovered']==done['result']
        assert result(client,'get_remote_job_cancellation',{**args,'request_key':str(uuid4())})['status']=='not_found'
        assert len(db('SELECT * FROM server_processing_cancel_requests'))==1


def test_limited_role_can_only_cancel_and_append_receipt(db,stores,pool):
    import hashlib
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from solo_leveling.interfaces.cli.processing_cancel_permissions import grant_processing_cancel_role
    from tests.integration.test_remote_processing_postgres import DSN
    owner='limited-'+uuid4().hex
    paper=make_paper(db,owner)
    job=stores[0].enqueue(owner,paper,'v1','parse',str(uuid4()),HASH)
    role='cancel_'+uuid4().hex[:12]
    with pool.connection() as conn:
        schema=conn.execute('SELECT current_schema() AS s').fetchone()['s']
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-cancel-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
        grant_processing_cancel_role(conn,schema,role)
    restricted=ConnectionPool(make_conninfo(DSN,user=role,password='local-cancel-only'),kwargs={'row_factory':dict_row,'options':f'-c search_path={schema}'},open=True)
    try:
        forbidden=["UPDATE server_processing_jobs SET status='succeeded',finished_at=clock_timestamp()",'UPDATE server_processing_jobs SET attempt=attempt+1','UPDATE server_papers SET paper_id=paper_id','DELETE FROM server_processing_cancel_requests','SELECT * FROM server_device_credentials','UPDATE server_dispatch_budget_limits SET reserved_input_tokens=0']
        for query in forbidden:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with restricted.connection() as conn: conn.execute(query)
        service=PostgresProcessingCancellationStore(restricted)
        key=str(uuid4())
        assert service.cancel(owner,job.job_id,key,1)['status']=='cancelled'
        assert service.lookup(owner,job.job_id,key)['applied_revision']==2
    finally:
        restricted.close()
        with pool.connection() as conn:
            for table in ('server_papers','server_processing_jobs'):
                conn.execute(sql.SQL('DROP TRIGGER {} ON {}').format(sql.Identifier('cancel_guard_'+hashlib.sha256(role.encode()).hexdigest()[:12]),sql.Identifier(table)))
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
