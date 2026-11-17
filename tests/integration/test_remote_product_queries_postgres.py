"""실제 ASGI 인증 경계와 PostgreSQL을 연결한다. 모델·provider 포트는 없다."""
# ruff: noqa: F811
import os
import time
from dataclasses import replace
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.integration.test_remote_processing_postgres import pool,db,stores  # noqa: F401
from tests.fixtures.pgvector_retrieval_contract import PgvectorContractStore
from tests.integration.test_remote_mcp_product_boundary import SETTINGS,call
from solo_leveling.application.evidence_qa.remote_contract import VerifiedRemoteIdentity
from solo_leveling.interfaces.http import create_app
from solo_leveling.interfaces.http.remote_mcp_auth import RemoteIdentityAccessToken
from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='실제 PostgreSQL DSN 필요')


class Verifier:
    def __init__(self,suffix=''): self.suffix=suffix
    async def verify_token(self,token):
        if token not in ('account-a','account-b'): return None
        value=RemoteIdentityAccessToken(token=token,client_id='test-client',subject=token,
            scopes=['spike:read'],resource=SETTINGS.resource_url,expires_at=int(time.time())+60)
        value._verified_identity=VerifiedRemoteIdentity(SETTINGS.issuer,token+self.suffix)
        return value


def result(client,name,args=None,token='account-a'):
    import json
    response=call(client,'tools/call',{'name':name,'arguments':args or {}},token=token)
    value=response.json()['result']
    assert not value.get('isError'),value
    return json.loads(value['content'][0]['text'])


def test_product_tools_auth_owner_pin_replay_and_revocation(db,stores,pool):
    remote=PgvectorContractStore(db,stores,pool)
    first=remote.publish()
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'합성 계정')",(remote.owner,))
    queries=RemoteProductQueries(pool)
    queries.owners.link_identity(remote.owner,VerifiedRemoteIdentity(SETTINGS.issuer,'account-a'))
    settings=replace(SETTINGS,product_reads_enabled=True)
    with TestClient(create_app(remote_mcp_settings=settings,remote_mcp_verifier=Verifier(),
                              remote_product_queries=queries),base_url=SETTINGS.resource_url[:-4]) as client:
        names={v['name'] for v in call(client,'tools/list').json()['result']['tools']}
        assert names=={'whoami','probe_card','probe_delay','list_remote_papers','open_remote_paper','get_remote_question'}
        listed=result(client,'list_remote_papers')
        assert listed['status']=='ok' and listed['papers'][0]['paper_id']==first.paper
        args={'paper_id':first.paper,'version_id':'v1','context_key':str(uuid4())}
        pinned=result(client,'open_remote_paper',args)
        assert pinned['publication_id']==first.publication
        remote.publish(100,base=first)
        assert result(client,'open_remote_paper',args)==pinned
        assert result(client,'open_remote_paper',args,token='account-b')=={'status':'not_found'}
        assert result(client,'get_remote_question',{'request_key':'missing'})=={'status':'not_found'}
        assert result(client,'get_remote_question',{'request_key':'missing'},token='account-b')=={'status':'not_found'}
        assert not db('SELECT execution_id FROM server_remote_executions')
        assert not db('SELECT attempt_id FROM server_provider_attempts')
        assert call(client,'tools/list',token='invalid').status_code==401
        queries.owners.revoke_identity(remote.owner,VerifiedRemoteIdentity(SETTINGS.issuer,'account-a'))
        assert result(client,'list_remote_papers')=={'status':'not_found'}


def test_product_flag_requires_backend():
    with pytest.raises(ValueError,match='remote_product_queries_required'):
        create_app(remote_mcp_settings=replace(SETTINGS,product_reads_enabled=True),remote_mcp_verifier=Verifier())


def test_saved_result_survives_new_app_and_owner_switch_without_execution(db,stores,pool):
    from tests.integration.test_remote_answer_store_postgres import setup_answer
    from solo_leveling.application.remote_replay import ExecutionLookup
    from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore
    intent,prepared=setup_answer(db,stores,pool)
    queries=RemoteProductQueries(pool)
    lookup=ExecutionLookup('alice','ask_paper',str(uuid4()),'input',prepared.binding.context_id)
    saved=queries.executions.create(lookup,intent.storage_consent_id,intent.ai_consent_id,'notice')
    current=queries.executions.claim('alice',saved.execution_id)
    expected=PostgresRemoteAnswerStore(pool).save(current,prepared)
    queries.owners.link_identity('alice',VerifiedRemoteIdentity(SETTINGS.issuer,'account-a-replay'))
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES ('bob','합성 계정') ON CONFLICT DO NOTHING")
    queries.owners.link_identity('bob',VerifiedRemoteIdentity(SETTINGS.issuer,'account-b-replay'))
    before=db('SELECT count(*) AS count FROM server_remote_executions')
    for _ in range(2):
        app=create_app(remote_mcp_settings=replace(SETTINGS,product_reads_enabled=True),
                       remote_mcp_verifier=Verifier('-replay'),remote_product_queries=RemoteProductQueries(pool))
        with TestClient(app,base_url=SETTINGS.resource_url[:-4]) as client:
            args={'request_key':lookup.request_key}
            restored=result(client,'get_remote_question',args)
            assert restored['result']==expected and restored['execution_id']==saved.execution_id
            assert result(client,'get_remote_question',args,token='account-b')=={'status':'not_found'}
            assert result(client,'get_remote_question',args)==restored
    assert db('SELECT count(*) AS count FROM server_remote_executions')==before
    assert not db('SELECT attempt_id FROM server_provider_attempts')
    db("UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s",(prepared.binding.paper_id,))
    app=create_app(remote_mcp_settings=replace(SETTINGS,product_reads_enabled=True),
                   remote_mcp_verifier=Verifier('-replay'),remote_product_queries=RemoteProductQueries(pool))
    with TestClient(app,base_url=SETTINGS.resource_url[:-4]) as client:
        assert result(client,'get_remote_question',args)=={'status':'not_found'}
