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
            scopes=['spike:read','papers:read'],resource=SETTINGS.resource_url,expires_at=int(time.time())+60)
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
        assert names=={'whoami','probe_card','probe_delay','list_remote_papers','open_remote_paper','get_remote_question','get_remote_evidence','get_remote_paper_detail','get_learning_path','get_prerequisites'}
        listed=result(client,'list_remote_papers')
        assert listed['status']=='ok' and listed['papers'][0]['paper_id']==first.paper
        args={'paper_id':first.paper,'version_id':'v1','context_key':str(uuid4())}
        pinned=result(client,'open_remote_paper',args)
        assert pinned['publication_id']==first.publication
        detail_args={'context_id':pinned['context_id']}
        assert result(client,'get_remote_paper_detail',detail_args)['result']['publication_id']==first.publication
        assert result(client,'get_remote_paper_detail',detail_args,token='account-b')=={'status':'not_found'}
        path = result(client,'get_learning_path',detail_args)
        assert path['result']['graph_status'] == 'no_graph'
        assert path['result']['steps'] == []
        assert result(client,'get_learning_path',detail_args,token='account-b')['status']=='not_found'
        assert result(client,'get_prerequisites',{**detail_args,'concept':'attention'},token='account-b')['status']=='not_found'
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


@pytest.mark.parametrize('intake,dispatch', [(True, True), (False, True), (True, False), (False, False)])
def test_saved_result_survives_new_app_and_owner_switch_without_execution(db,stores,pool,intake,dispatch):
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
    db("""INSERT INTO server_dispatch_controls(control_id,generation,intake_enabled,dispatch_enabled)
          VALUES ('provider_dispatch',1,%s,%s) ON CONFLICT(control_id)
          DO UPDATE SET intake_enabled=EXCLUDED.intake_enabled,dispatch_enabled=EXCLUDED.dispatch_enabled""",
       (intake, dispatch))
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
    assert db("SELECT intake_enabled,dispatch_enabled FROM server_dispatch_controls WHERE control_id='provider_dispatch'") == [
        dict(intake_enabled=intake, dispatch_enabled=dispatch)]
    db("UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s",(prepared.binding.paper_id,))
    app=create_app(remote_mcp_settings=replace(SETTINGS,product_reads_enabled=True),
                   remote_mcp_verifier=Verifier('-replay'),remote_product_queries=RemoteProductQueries(pool))
    with TestClient(app,base_url=SETTINGS.resource_url[:-4]) as client:
        assert result(client,'get_remote_question',args)=={'status':'not_found'}


def test_limited_database_role_can_pin_and_cannot_modify_other_data(db,stores,pool):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    from tests.integration.test_remote_processing_postgres import DSN
    remote=PgvectorContractStore(db,stores,pool);first=remote.publish()
    role='mcp_read_'+uuid4().hex[:12]
    tables=('server_remote_identities','server_papers','server_processing_versions',
        'server_processing_publications','server_embedding_sets','server_execution_contexts','server_parse_revisions',
        'server_remote_executions','server_provider_attempts')
    with pool.connection() as conn:
        schema=conn.execute('SELECT current_schema() AS s').fetchone()['s']
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'local-mcp-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
        for table in tables:
            conn.execute(sql.SQL('GRANT SELECT ON {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT INSERT ON {}.server_execution_contexts TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
        conn.execute(sql.SQL('GRANT UPDATE(paper_id) ON {}.server_papers TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
        conn.execute(sql.SQL("""CREATE FUNCTION {}.mcp_read_lock_guard() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF current_user=TG_ARGV[0] THEN RAISE EXCEPTION 'mcp_readonly_table' USING ERRCODE='42501'; END IF;
            RETURN NEW; END $$""").format(sql.Identifier(schema)))
        conn.execute(sql.SQL('CREATE TRIGGER mcp_read_lock_guard BEFORE UPDATE ON {}.server_papers FOR EACH ROW EXECUTE FUNCTION {}.mcp_read_lock_guard({})').format(sql.Identifier(schema),sql.Identifier(schema),sql.Literal(role)))
    restricted=ConnectionPool(make_conninfo(DSN,user=role,password='local-mcp-only'),kwargs={'row_factory':dict_row,'options':f'-c search_path={schema}'},open=True)
    try:
        queries=RemoteProductQueries(restricted)
        pinned=queries.contexts.pin(remote.owner,str(uuid4()),first.paper,'v1')
        assert pinned.publication_id==first.publication
        assert queries.contexts.resolve(remote.owner,pinned.context_id)==pinned
        queries.owner=lambda: remote.owner
        assert queries.detail(pinned.context_id)['result']['publication_id']==first.publication
        for statement in ('SELECT * FROM web_sessions','SELECT * FROM server_device_credentials',
            'DELETE FROM server_execution_contexts','UPDATE server_papers SET paper_id=paper_id',
            "UPDATE server_dispatch_controls SET dispatch_enabled=true"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with restricted.connection() as conn:conn.execute(statement)
    finally:
        restricted.close()
        with pool.connection() as conn:
            conn.execute('DROP TRIGGER mcp_read_lock_guard ON server_papers')
            conn.execute('DROP FUNCTION mcp_read_lock_guard()')
            conn.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_spike_scope_alone_cannot_read_product_data(pool):
    class SpikeVerifier(Verifier):
        async def verify_token(self,token):
            value=await super().verify_token(token)
            if value:value.scopes=['spike:read']
            return value
    queries=RemoteProductQueries(pool)
    from unittest.mock import Mock
    queries.owners.resolve_owner=Mock(side_effect=AssertionError('scope 검사 전에 DB 조회 금지'))
    app=create_app(remote_mcp_settings=replace(SETTINGS,product_reads_enabled=True),
                   remote_mcp_verifier=SpikeVerifier(),remote_product_queries=queries)
    with TestClient(app,base_url=SETTINGS.resource_url[:-4]) as client:
        assert call(client,'tools/list').status_code==403
        metadata=client.get('/.well-known/oauth-protected-resource/mcp').json()
        assert 'papers:read' in metadata['scopes_supported']
    queries.owners.resolve_owner.assert_not_called()


def test_whole_paper_detail_keeps_publication_and_pages_sections(db, stores, pool, monkeypatch):
    import json
    remote = PgvectorContractStore(db, stores, pool)
    from tests.fixtures import pgvector_retrieval_contract as fixture
    original_ready = fixture.mark_ready
    sections = [dict(section_id=f's{i}', parse_revision_id=None,
                     title=f'합성 절 {i}', ordinal=i, level=1, start_pdf_page=1, end_pdf_page=1)
                for i in range(21)]
    def with_sections(db, parse, translation, embedding):
        saved = [dict(s, parse_revision_id=parse) for s in sections]
        db('UPDATE server_parse_revisions SET document_metadata=%s::jsonb WHERE parse_revision_id=%s',
           (json.dumps({'sections': list(reversed(saved))}), parse))
        original_ready(db, parse, translation, embedding)
    monkeypatch.setattr(fixture, 'mark_ready', with_sections)
    first = remote.publish()
    queries = RemoteProductQueries(pool)
    queries.owner = lambda: remote.owner
    pinned = queries.contexts.pin(remote.owner, str(uuid4()), first.paper, 'v1')
    before = queries.detail(pinned.context_id)
    assert before['status'] == 'ok'
    detail = before['result']
    assert detail['publication_id'] == first.publication
    assert len(detail['section_catalog']) == 20
    assert detail['section_catalog'][0]['section_id'] == 's0'
    assert detail['next_section_offset'] == 20
    last = queries.detail(pinned.context_id, 20)['result']
    assert [s['section_id'] for s in last['section_catalog']] == ['s20']
    assert last['next_section_offset'] is None
    assert detail['summary'] is None and detail['keywords'] == []
    assert detail['annotation_lookup']['arguments']['context_id'] == pinned.context_id
    remote.publish(100, base=first)
    assert queries.detail(pinned.context_id) == before
    queries.owner = lambda: 'other-owner'
    assert queries.detail(pinned.context_id) == {'status': 'not_found'}
    queries.owner = lambda: remote.owner
    monkeypatch.setattr(fixture, 'mark_ready', original_ready)
    empty_paper = remote.publish(200)
    empty = queries.detail(empty_paper.context)['result']
    assert empty['section_catalog'] == []
    assert 'section_catalog_not_available' in empty['limitations']
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE paper_id=%s', (first.paper,))
    assert queries.detail(pinned.context_id) == {'status': 'not_found'}
    assert not db('SELECT attempt_id FROM server_provider_attempts')
