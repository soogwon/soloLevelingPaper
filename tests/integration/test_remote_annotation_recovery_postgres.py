"""인증 transport 전 단계의 복구 포트를 실제 격리 DB에서 검증한다."""
# ruff: noqa: F811
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_annotations_postgres import context, annotation  # noqa: F401
from solo_leveling.interfaces.http.remote_annotations import (
    PostgresRemoteAnnotationStore, RemoteAnnotationRecoveryAmbiguous,
)

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def test_lost_response_new_store_recovers_current_revision_then_confirms(pool, db, context):
    owner, context_id, _ = context
    original = PostgresRemoteAnnotationStore(pool)
    value = annotation(context)
    saved = original.create(owner, 'service', context_id, 'lost-response', value)
    db("UPDATE server_context_annotations SET body='현재 본문',revision=2 WHERE annotation_id=%s",
       (saved.annotation.annotation_id,))
    restored = PostgresRemoteAnnotationStore(pool)
    recovered = restored.recover_request(owner, 'service', context_id, value)
    assert recovered.request_key == 'lost-response' and recovered.recovery == 'unique_unconfirmed'
    assert recovered.annotation.annotation_id == saved.annotation.annotation_id
    assert recovered.annotation.body == '현재 본문' and recovered.annotation.revision == 2
    assert not restored.confirm_request(owner, 'service', context_id, 'lost-response', saved.annotation.annotation_id, 1)
    assert restored.recover_request(owner, 'service', context_id, value) is not None
    assert restored.confirm_request(owner, 'service', context_id, 'lost-response', saved.annotation.annotation_id, 2)
    before = db("SELECT client_confirmed_at FROM server_annotation_create_requests WHERE annotation_id=%s", (saved.annotation.annotation_id,))
    assert restored.confirm_request(owner, 'service', context_id, 'lost-response', saved.annotation.annotation_id, 2)
    assert before == db("SELECT client_confirmed_at FROM server_annotation_create_requests WHERE annotation_id=%s", (saved.annotation.annotation_id,))
    assert restored.recover_request(owner, 'service', context_id, value) is None
    exact = restored.lookup_request(owner, 'service', context_id, 'lost-response')
    assert exact.recovery == 'exact' and exact.annotation.revision == 2
    assert db("SELECT count(*) n FROM server_context_annotations WHERE owner_user_id=%s", (owner,))[0]['n'] == 1


@pytest.mark.parametrize('scope', ['owner', 'service', 'context'])
def test_cross_scope_cannot_read_or_confirm(pool, db, context, scope):
    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool); value = annotation(context)
    saved = store.create(owner, 'service', context_id, 'key', value)
    args = [owner, 'service', context_id]
    args[['owner', 'service', 'context'].index(scope)] = '00000000-0000-0000-0000-000000000000'
    assert store.lookup_request(*args, 'key') is None
    assert store.recover_request(*args, value) is None
    assert not store.confirm_request(*args, 'key', saved.annotation.annotation_id, 1)
    assert store.recover_request(owner, 'service', context_id, value).request_key == 'key'


def test_ambiguous_input_never_selects_or_creates_key(pool, db, context):
    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool); value = annotation(context)
    for key in ('first', 'second'): store.create(owner, 'service', context_id, key, value)
    with pytest.raises(RemoteAnnotationRecoveryAmbiguous, match='annotation_recovery_ambiguous'):
        store.recover_request(owner, 'service', context_id, value)
    assert store.recover_request(owner, 'service', context_id, annotation(context, body='다름')) is None
    assert db("SELECT count(*) n FROM server_context_annotations WHERE owner_user_id=%s", (owner,))[0]['n'] == 2


def test_deleted_annotation_retains_recovery_but_paper_tombstone_hides_it(pool, db, context):
    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool); value = annotation(context)
    saved = store.create(owner, 'service', context_id, 'key', value)
    assert store.mark_deleted(owner, saved.annotation.annotation_id)
    for row in (store.lookup_request(owner, 'service', context_id, 'key'),
                store.recover_request(owner, 'service', context_id, value)):
        assert row.state == 'deleted' and row.annotation.body == '' and row.annotation.segments == ()
        assert row.annotation.revision == 2
    db("UPDATE server_papers SET deleted_at=clock_timestamp() WHERE owner_user_id=%s", (owner,))
    assert store.lookup_request(owner, 'service', context_id, 'key') is None
    assert store.recover_request(owner, 'service', context_id, value) is None
    assert not store.confirm_request(owner, 'service', context_id, 'key', saved.annotation.annotation_id, 2)


def test_confirmation_and_delete_converge_without_deadlock(pool, context):
    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool)
    saved = store.create(owner, 'service', context_id, 'key', annotation(context))
    with ThreadPoolExecutor(max_workers=2) as workers:
        confirmation = workers.submit(store.confirm_request, owner, 'service', context_id, 'key', saved.annotation.annotation_id, 1)
        deletion = workers.submit(store.mark_deleted, owner, saved.annotation.annotation_id)
        assert isinstance(confirmation.result(timeout=5), bool)
        assert deletion.result(timeout=5)
    exact = store.lookup_request(owner, 'service', context_id, 'key')
    assert exact.state == 'deleted' and exact.annotation.revision == 2


@pytest.mark.parametrize('revision', [True, 0, -1, '1'])
def test_invalid_confirmation_rejected(pool, context, revision):
    owner, context_id, _ = context
    with pytest.raises(ValueError, match='remote_annotation_confirmation_invalid'):
        PostgresRemoteAnnotationStore(pool).confirm_request(owner, 'service', context_id, 'key', 'unused', revision)


@pytest.mark.parametrize('operation', ['lookup', 'recover', 'confirm'])
def test_parent_tombstone_committed_while_waiting_hides_result(pool, db, context, operation):
    from threading import Event
    owner, context_id, _ = context
    store = PostgresRemoteAnnotationStore(pool); value = annotation(context)
    saved = store.create(owner, 'service', context_id, 'key', value)
    entered = Event()
    def invoke():
        entered.set()
        if operation == 'lookup': return store.lookup_request(owner, 'service', context_id, 'key')
        if operation == 'recover': return store.recover_request(owner, 'service', context_id, value)
        return store.confirm_request(owner, 'service', context_id, 'key', saved.annotation.annotation_id, 1)
    with ThreadPoolExecutor(max_workers=1) as workers:
        with pool.connection() as conn:
            conn.execute('SELECT paper_id FROM server_papers WHERE owner_user_id=%s FOR UPDATE', (owner,))
            future = workers.submit(invoke)
            assert entered.wait(2)
            conn.execute('UPDATE server_papers SET deleted_at=clock_timestamp() WHERE owner_user_id=%s', (owner,))
        result = future.result(timeout=5)
        assert result is (False if operation == 'confirm' else None)
    assert db('SELECT client_confirmed_at FROM server_annotation_create_requests WHERE annotation_id=%s',
              (saved.annotation.annotation_id,))[0]['client_confirmed_at'] is None
