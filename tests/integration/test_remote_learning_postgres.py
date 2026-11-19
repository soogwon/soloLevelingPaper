"""원격 학습 기록은 명시적 시작만 생성하고 원래 게시에 고정한다."""
# ruff: noqa: F811
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_annotations_postgres import context, annotation  # noqa: F401
from solo_leveling.interfaces.http.remote_learning import PostgresRemoteLearningStore, RemoteLearningConflict
from solo_leveling.interfaces.http.remote_annotations import PostgresRemoteAnnotationStore

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def test_explicit_start_and_concurrent_resume(pool, db, context):
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    assert store.get(owner, key) is None
    assert db('SELECT count(*) n FROM server_remote_learning_records WHERE owner_user_id=%s', (owner,))[0]['n'] == 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(lambda _: store.start(owner, key), range(8)))
    assert len({r['learning_record_id'] for r in rows}) == 1
    assert all(r == rows[0] for r in rows)
    assert rows[0]['origin'] == 'remote' and rows[0]['status'] == 'reading'
    assert PostgresRemoteLearningStore(pool).get(owner, key) == rows[0]
    assert db('SELECT count(*) n FROM server_learning_records WHERE owner_user_id=%s', (owner,))[0]['n'] == 0


def test_complete_reopen_keeps_annotation_and_identity(pool, context):
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    initial = store.start(owner, key)
    annotations = PostgresRemoteAnnotationStore(pool)
    saved = annotations.create(owner, 'product', key, 'key', annotation(context))
    done = store.set_status(owner, key, status='completed', expected_revision=1)
    assert done['revision'] == 2
    assert store.start(owner, key) == done
    with pytest.raises(RemoteLearningConflict):
        store.set_status(owner, key, status='reading', expected_revision=1)
    reopened = store.set_status(owner, key, status='reading', expected_revision=2)
    assert reopened['learning_record_id'] == initial['learning_record_id']
    assert reopened['revision'] == 3
    assert annotations.lookup_request(owner, 'product', key, 'key').annotation == saved.annotation


def test_wrong_owner_and_tombstone_cannot_start_or_resume(pool, db, context):
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    assert store.start('other', key) is None
    assert store.get('other', key) is None
    assert store.set_status('other', key, status='reading', expected_revision=1) is None
    store.start(owner, key)
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE owner_user_id=%s', (owner,))
    assert store.get(owner, key) is None
    assert store.start(owner, key) is None
    assert store.set_status(owner, key, status='completed', expected_revision=1) is None


def test_binding_immutable_and_context_cleanup_cascades(pool, db, context):
    import psycopg
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    store.start(owner, key)
    with pytest.raises(psycopg.errors.CheckViolation):
        db('UPDATE server_remote_learning_records SET context_id=%s WHERE owner_user_id=%s', (str(uuid4()), owner))
    db('DELETE FROM server_execution_contexts WHERE owner_user_id=%s AND context_id=%s', (owner, key))
    assert db('SELECT count(*) n FROM server_remote_learning_records WHERE owner_user_id=%s', (owner,))[0]['n'] == 0


def test_new_context_does_not_rebind_old_learning(pool, db, context):
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    old = store.start(owner, key)
    new_key = str(uuid4())
    db('''INSERT INTO server_execution_contexts
        (owner_user_id,context_id,paper_id,server_version_id,publication_id,parse_revision_id)
        SELECT owner_user_id,%s,paper_id,server_version_id,publication_id,parse_revision_id
        FROM server_execution_contexts WHERE owner_user_id=%s AND context_id=%s''', (new_key, owner, key))
    new = store.start(owner, new_key)
    assert old['learning_record_id'] != new['learning_record_id']
    assert store.get(owner, key) == old


@pytest.mark.parametrize('revision', [True, 0, -1, '1'])
def test_invalid_revision_does_not_change_record(pool, context, revision):
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    original = store.start(owner, key)
    with pytest.raises(ValueError):
        store.set_status(owner, key, status='completed', expected_revision=revision)
    assert store.get(owner, key) == original


def test_republication_keeps_old_record_bound(pool, db, context, stores):
    from tests.integration.test_remote_processing_postgres import claimed_job, make_revision_set, publish, mark_ready
    from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    old = store.start(owner, key)
    job = claimed_job(stores[0], owner, old['paper_id'])
    revisions = make_revision_set(db, owner, old['paper_id'], job, chunks=1, ready=False)
    db("UPDATE server_embedding_sets SET model_fingerprint='{\"model\":\"synthetic\"}' WHERE embedding_set_id=%s", (revisions[2],))
    mark_ready(db, *revisions[:3])
    publication = publish(stores[1], owner, old['paper_id'], revisions, job)
    new_key = str(uuid4())
    PostgresRemoteContextResolver(pool).pin(owner, new_key, old['paper_id'], 'v1')
    new = store.start(owner, new_key)
    assert new['publication_id'] == publication.publication_id
    assert new['publication_id'] != old['publication_id']
    assert store.start(owner, key) == old


def test_cleanup_backend_removes_remote_learning(pool, db, context):
    from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore
    from solo_leveling.infrastructure.storage.remote_deletion_backend import PostgresObjectDeletionBackend
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    row = store.start(owner, key)
    PostgresRemoteDeletionStore(pool).request(owner, row['paper_id'], 'delete-learning')
    PostgresObjectDeletionBackend(pool, None).delete_derived(owner, row['paper_id'])
    assert db('SELECT count(*) n FROM server_remote_learning_records WHERE owner_user_id=%s', (owner,))[0]['n'] == 0
    assert store.start(owner, key) is None


def test_paper_context_list_owner_version_cursor_and_deletion(pool, db, context):
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    first = store.start(owner, key)
    paper, version = first['paper_id'], first['server_version_id']
    assert store.list_for_paper('other', paper, version)['items'] == []
    assert store.list_for_paper(owner, paper, str(uuid4()))['items'] == []
    keys = [key]
    for _ in range(2):
        new_key = str(uuid4()); keys.append(new_key)
        db('''INSERT INTO server_execution_contexts
            (owner_user_id,context_id,paper_id,server_version_id,publication_id,parse_revision_id)
            SELECT owner_user_id,%s,paper_id,server_version_id,publication_id,parse_revision_id
            FROM server_execution_contexts WHERE owner_user_id=%s AND context_id=%s''', (new_key, owner, key))
        store.start(owner, new_key)
    # 대표 맥락은 UUID 크기가 아니라 마지막 진행 저장 시각으로 정한다.
    ordered = [keys[1], keys[2], keys[0]]
    for seconds, item in enumerate(reversed(ordered), start=1):
        db('''UPDATE server_remote_learning_records
            SET updated_at=TIMESTAMPTZ '2026-11-20 00:00:00+00' + %s * INTERVAL '1 second',
                last_pdf_page=%s
            WHERE owner_user_id=%s AND context_id=%s''', (seconds, seconds, owner, item))
    found = []; cursor = None
    pages = []
    while True:
        page = store.list_for_paper(owner, paper, version, after=cursor, limit=1)
        pages.extend(page['items'])
        found.extend(r['context_id'] for r in page['items'])
        cursor = page['next_cursor']
        if cursor is None: break
    assert found == ordered
    assert pages[0]['updated_at'] > pages[1]['updated_at'] > pages[2]['updated_at']
    assert pages[0]['last_position'] == {'pdf_page': 3}
    assert store.list_for_paper(owner, paper, version, after=str(uuid4())) == {
        'items': [], 'next_cursor': None}
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE owner_user_id=%s', (owner,))
    assert store.list_for_paper(owner, paper, version)['items'] == []


def test_dashboard_pagination_read_states_and_no_implicit_start(pool, db, context):
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    keys = [key] + [str(uuid4()) for _ in range(20)]
    for item in keys[1:]:
        db('''INSERT INTO server_execution_contexts
            (owner_user_id,context_id,paper_id,server_version_id,publication_id,parse_revision_id)
            SELECT owner_user_id,%s,paper_id,server_version_id,publication_id,parse_revision_id
            FROM server_execution_contexts WHERE owner_user_id=%s AND context_id=%s''', (item, owner, key))
    first = store.dashboard(owner)
    second = store.dashboard(owner, after=first['next_cursor'])
    assert len(first['items']) == 20 and len(second['items']) == 1
    assert second['next_cursor'] is None
    assert {r['context_id'] for r in first['items'] + second['items']} == set(keys)
    assert all(r['reading_status']=='not_started' and r['processing_status']=='published' for r in first['items'])
    assert db('SELECT count(*) n FROM server_remote_learning_records WHERE owner_user_id=%s', (owner,))[0]['n'] == 0
    store.start(owner,key)
    assert store.reading_state(owner,key)['reading_status']=='reading'
    store.set_status(owner,key,status='completed',expected_revision=1)
    state = store.reading_state(owner,key)
    assert state['reading_status']=='completed' and state['revision']==2
    assert state['last_position'] is None and state['quiz_summary'] is None
    assert store.reading_state('other',key) is None
    assert store.dashboard('other')['items']==[]
    db('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE owner_user_id=%s', (owner,))
    assert store.dashboard(owner)['items']==[]


def test_dashboard_counts_exclude_deleted_records(pool, db, context):
    owner, key, _ = context
    store = PostgresRemoteLearningStore(pool)
    annotations = PostgresRemoteAnnotationStore(pool)
    from dataclasses import replace
    annotations.create(owner,'dashboard',key,'note',annotation(context))
    created = annotations.create(owner,'dashboard',key,'new',replace(annotation(context), kind='highlight')).annotation
    assert store.reading_state(owner,key)['note_count']==1
    assert store.reading_state(owner,key)['highlight_count']==1
    annotations.delete_annotation(owner,key,created.annotation_id,expected_revision=1)
    assert store.reading_state(owner,key)['highlight_count']==0


def test_dashboard_real_mcp_serialization(pool, context, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from mcp.server.fastmcp import FastMCP
    from solo_leveling.interfaces.mcp import remote_annotations as tools
    from tests.unit.test_remote_annotation_tools import token
    owner, key, _ = context
    monkeypatch.setattr(tools,'get_access_token',lambda:token(['annotations:read']))
    service = SimpleNamespace(learning=PostgresRemoteLearningStore(pool))
    server = FastMCP('합성 대시보드')
    tools.register_annotation_tools(server,SimpleNamespace(owner=lambda:owner),service)

    async def verify():
        registered={t.name:t for t in await server.list_tools()}
        assert registered['get_remote_learning_dashboard'].annotations.readOnlyHint
        reply=await server.call_tool('get_remote_learning_dashboard',{})
        assert 'not_started' in str(reply) and key in str(reply)
        assert service.learning.get(owner,key) is None
        service.learning.start(owner,key)
        reply=await server.call_tool('get_remote_reading_state',{'context_id':key})
        assert 'reading' in str(reply) and 'last_learning_updated_at' in str(reply)
    asyncio.run(verify())
