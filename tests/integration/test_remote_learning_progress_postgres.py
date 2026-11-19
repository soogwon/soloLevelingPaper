"""실제 DB에서 위치·상태 재전송과 오래된 기기의 덮어쓰기를 차단한다."""
# ruff: noqa: F811
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_processing_publication_postgres import prepared, publish
from solo_leveling.interfaces.http.remote_learning import PostgresRemoteLearningStore, RemoteLearningConflict

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


@pytest.fixture
def context(pool, db):
    publisher, source, digest = prepared(pool, db)
    publication = publish(publisher, source, digest)
    key = str(uuid4())
    db("""INSERT INTO server_execution_contexts
        (context_id,owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id)
        VALUES (%s,%s,%s,%s,%s,%s)""", (key,source.owner_user_id,source.paper_id,
            source.version_id,publication.publication_id,source.parse_revision_id))
    return source.owner_user_id,key,None


def setup(pool, db, context):
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    store.start(owner, key)
    return store, owner, key


def test_concurrent_same_request_replays_and_late_retry_returns_current(pool, db, context):
    store, owner, key = setup(pool, db, context)
    args = dict(request_key=str(uuid4()),status='completed',pdf_page=2,expected_revision=1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:store.set_progress(owner,key,**args),range(2)))
    assert sorted(r['replayed'] for r in results)==[False,True]
    assert {r['applied_revision'] for r in results}=={2}
    assert store.reading_state(owner,key)['last_position']=={'pdf_page':2}
    reopened=store.set_progress(owner,key,request_key=str(uuid4()),status='reading',pdf_page=None,expected_revision=2)
    assert reopened['state']['revision']==3
    late=PostgresRemoteLearningStore(pool).set_progress(owner,key,**args)
    assert late['replayed'] and late['applied_revision']==2
    assert late['state']['status']=='reading' and late['state']['revision']==3
    assert late['state']['last_pdf_page'] is None
    assert db('SELECT count(*) n FROM server_remote_learning_requests WHERE owner_user_id=%s',(owner,))[0]['n']==2


def test_revision_payload_page_and_owner_fail_without_writes(pool, db, context):
    store,owner,key=setup(pool,db,context)
    args=dict(request_key=str(uuid4()),status='reading',pdf_page=1,expected_revision=1)
    assert store.set_progress('other',key,**args) is None
    with pytest.raises(ValueError):store.set_progress(owner,key,**{**args,'pdf_page':4})
    assert store.get(owner,key)['revision']==1
    store.set_progress(owner,key,**args)
    with pytest.raises(RemoteLearningConflict):store.set_progress(owner,key,**{**args,'pdf_page':2})
    with pytest.raises(RemoteLearningConflict):store.set_progress(owner,key,**{**args,'request_key':str(uuid4())})
    assert store.get(owner,key)['revision']==2
    db('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE owner_user_id=%s',(owner,))
    assert store.set_progress(owner,key,**args) is None



def test_reading_card_mcp_conflict_replay_and_binding(pool, db, context, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from mcp.server.fastmcp import FastMCP
    from solo_leveling.interfaces.mcp import remote_annotations as tools
    from tests.unit.test_remote_annotation_tools import token

    store, owner, key = setup(pool, db, context)
    current = token(['annotations:read'])
    monkeypatch.setattr(tools, 'get_access_token', lambda: current)
    service = SimpleNamespace(learning=store, service_id='remote-paper')
    server = FastMCP('읽기 카드')
    tools.register_annotation_tools(server, SimpleNamespace(owner=lambda: owner), service)

    async def verify():
        registered = {tool.name: tool for tool in await server.list_tools()}
        read = registered['show_remote_reading_progress']
        save = registered['save_remote_reading_progress_card']
        assert read.meta['ui']['resourceUri'] == 'ui://remote-learning/reading-progress.html'
        assert save.meta['ui']['visibility'] == ['app']
        resources = await server.read_resource(read.meta['ui']['resourceUri'])
        assert '최근 위치 저장' in resources[0].content
        _, opened = await server.call_tool(read.name, {'context_id': key})
        assert opened['result']['can_write'] is False
        binding = opened['result']['binding']
        args = dict(context_id=key, request_key=str(uuid4()), status='completed', pdf_page=1,
                    expected_revision=1, expected_binding=binding)
        _, denied = await server.call_tool(save.name, args)
        assert denied['status'] == 'not_authorized'
        current.scopes.append('annotations:write')
        _, completed = await server.call_tool(save.name, args)
        assert completed['result']['state']['last_pdf_page'] == 1
        assert completed['result']['applied_revision'] == 2
        _, replay = await server.call_tool(save.name, args)
        assert replay['result']['replayed'] is True

        args.update(request_key=str(uuid4()), status='reading')
        _, conflict = await server.call_tool(save.name, args)
        assert conflict['status'] == 'conflict'
        _, refreshed = await server.call_tool(read.name, {'context_id': key, 'expected_binding': binding})
        assert refreshed['result']['state']['reading_status'] == 'completed'
        assert refreshed['result']['state']['revision'] == 2

        current.token = 'renewed'
        args['expected_revision'] = 2
        _, changed = await server.call_tool(save.name, args)
        assert changed['result'] == {'connection_changed': True}
        assert service.learning.reading_state(owner, key)['revision'] == 2

    asyncio.run(verify())
