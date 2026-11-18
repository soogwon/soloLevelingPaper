"""원격 CRUD의 조건부 쓰기·고정 근거·삭제 이력·목록을 검증한다."""
# ruff: noqa: F811
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_annotations_postgres import context, annotation  # noqa: F401
from solo_leveling.interfaces.http.remote_learning import PostgresRemoteLearningStore
from solo_leveling.interfaces.http.remote_annotations import (
    PostgresRemoteAnnotationStore, RemoteAnnotationConflict, RemoteAnnotationDeleted, RemoteAnnotationReferenceError,
)

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def test_learning_required_and_revision_crud(pool, db, context):
    owner, key, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    value = annotation(context)
    with pytest.raises(RemoteAnnotationReferenceError):
        store.create(owner, 'product', key, 'key', value, require_learning=True)
    assert db('SELECT count(*) n FROM server_annotation_create_requests WHERE owner_user_id=%s', (owner,))[0]['n'] == 0
    PostgresRemoteLearningStore(pool).start(owner, key)
    saved = store.create(owner, 'product', key, 'key', value, require_learning=True).annotation
    changed = store.update_annotation(owner, key, saved.annotation_id, body='수정 메모', step=None, expected_revision=1)
    assert changed.revision == 2 and changed.segments == saved.segments
    with pytest.raises(RemoteAnnotationConflict):
        store.delete_annotation(owner, key, saved.annotation_id, expected_revision=1)
    assert store.get_annotation('other', key, saved.annotation_id) is None
    deleted = store.delete_annotation(owner, key, saved.annotation_id, expected_revision=2)
    assert deleted.revision == 3 and deleted.body == '' and deleted.segments == ()
    assert store.get_annotation(owner, key, saved.annotation_id) is None
    assert store.list_annotations(owner, key)['items'] == []
    assert store.lookup_request(owner, 'product', key, 'key').state == 'deleted'
    with pytest.raises(RemoteAnnotationDeleted):
        store.create(owner, 'product', key, 'key', value, require_learning=True)


def test_same_revision_concurrent_update_has_one_winner(pool, context):
    owner, key, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    saved = store.create(owner, 'product', key, 'key', annotation(context)).annotation
    def update(body):
        try:
            return store.update_annotation(owner, key, saved.annotation_id, body=body, step=None, expected_revision=1)
        except RemoteAnnotationConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = list(executor.map(update, ['첫째', '둘째']))
    assert sum(r is not None for r in result) == 1
    assert store.get_annotation(owner, key, saved.annotation_id).revision == 2


def test_keyset_listing_has_no_duplicate_or_missing_records(pool, context):
    owner, key, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    expected = {store.create(owner, 'product', key, f'key-{i}', annotation(context)).annotation.annotation_id for i in range(51)}
    seen = []
    cursor = None
    while True:
        page = store.list_annotations(owner, key, after=cursor, limit=20)
        seen += [r.annotation_id for r in page['items']]
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 51 and set(seen) == expected
    assert store.list_annotations('other', key) is None


@pytest.mark.parametrize('target', ['original', 'translation'])
def test_highlight_preserves_evidence_and_scope_on_update(pool, context, target):
    from solo_leveling.interfaces.http.remote_annotations import RemoteAnnotationInput
    owner, key, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    source = annotation(context, target=target)
    value = RemoteAnnotationInput('highlight', '', source.segments, creation_origin='user')
    PostgresRemoteLearningStore(pool).start(owner, key)
    saved = store.create(owner, 'product', key, 'highlight', value, require_learning=True).annotation
    updated = store.update_annotation(owner, key, saved.annotation_id, body='강조 설명', step=None, expected_revision=1)
    assert updated.kind == 'highlight' and updated.creation_origin == 'user'
    assert updated.segments == saved.segments and updated.segments[0]['target'] == target
    assert store.delete_annotation('other', key, saved.annotation_id, expected_revision=2) is None
    assert store.get_annotation(owner, key, saved.annotation_id) == updated
