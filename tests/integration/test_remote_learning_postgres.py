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
    found = []; cursor = None
    while True:
        page = store.list_for_paper(owner, paper, version, after=cursor, limit=1)
        found.extend(r['context_id'] for r in page['items'])
        cursor = page['next_cursor']
        if cursor is None: break
    assert found == sorted(keys)
    db('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE owner_user_id=%s', (owner,))
    assert store.list_for_paper(owner, paper, version)['items'] == []
