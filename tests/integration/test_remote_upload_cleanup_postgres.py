"""임시 객체 삭제와 DB 확정의 실패·재시도를 실제 PostgreSQL에서 확인한다."""
# ruff: noqa: F811
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db  # noqa: F401
from solo_leveling.interfaces.http.remote_uploads import PostgresRemoteUploadIntentStore, RemoteUploadVerificationService
from solo_leveling.interfaces.http.remote_upload_consents import PostgresRemoteUploadConsentStore

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


@pytest.mark.parametrize('failure', ['object', 'ack', None])
def test_cleanup_acknowledges_success_and_retries_partial_failure(pool, db, failure):
    from unittest.mock import Mock
    from uuid import uuid4

    owner = 'cleanup-ack-' + str(uuid4())
    db('INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,\'합성\')', (owner,))
    store = PostgresRemoteUploadIntentStore(pool)
    intent = store.create(owner, 'request', 'paper.pdf', 10, 'application/pdf', 'a' * 64)
    PostgresRemoteUploadConsentStore(pool).grant(
        owner, intent.intent_id, 'storage', 'cleanup-consent')
    reservation, _ = store.reserve_upload(owner, intent.intent_id)
    db("UPDATE server_remote_upload_intents SET expires_at=clock_timestamp()-interval '1 hour' WHERE intent_id=%s",
       (intent.intent_id,))
    objects = Mock()
    original_ack = store.mark_expired_object_deleted
    if failure == 'object':
        objects.delete_upload.side_effect = RuntimeError('합성 객체 삭제 실패')
    elif failure == 'ack':
        store.mark_expired_object_deleted = Mock(side_effect=RuntimeError('합성 DB 확정 실패'))
    service = RemoteUploadVerificationService(store, objects)
    result = service.cleanup_expired()
    assert result == {'claimed': 1, 'deleted': int(failure is None), 'failed': int(failure is not None)}
    if failure:
        assert db('SELECT status FROM server_pdf_uploads WHERE upload_id=%s', (reservation.upload_id,))[0]['status'] == 'pending'
        objects.delete_upload.side_effect = None
        store.mark_expired_object_deleted = original_ack
        assert service.cleanup_expired() == {'claimed': 1, 'deleted': 1, 'failed': 0}
    row = db('SELECT status,failure_reason FROM server_pdf_uploads WHERE upload_id=%s', (reservation.upload_id,))[0]
    assert row == {'status': 'failed', 'failure_reason': 'upload_expired'}
    calls = objects.delete_upload.call_count
    assert service.cleanup_expired() == {'claimed': 0, 'deleted': 0, 'failed': 0}
    assert objects.delete_upload.call_count == calls
