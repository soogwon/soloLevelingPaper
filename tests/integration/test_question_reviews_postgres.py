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
