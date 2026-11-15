"""원격 업로드 응답 유실 복구를 실제 DB와 합성 객체 저장소로 확인한다."""
# ruff: noqa: F811
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db  # noqa: F401
from solo_leveling.interfaces.http.remote_uploads import PostgresRemoteUploadIntentStore, RemoteUploadVerificationService
from solo_leveling.interfaces.http.object_storage import StoredObject

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


def test_verified_result_replays_after_cleanup_failure_and_expiry(pool, db):
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES ('upload-owner','합성 사용자')")
    store = PostgresRemoteUploadIntentStore(pool)
    intent = store.create('upload-owner', 'upload-key', 'paper.pdf', 10, 'application/pdf', 'a' * 64)
    class Objects:
        copies = 0
        def inspect_upload(self, pending):
            return StoredObject(pending.object_key, 10, 'application/pdf', 'a' * 64, True)
        def finalize_upload(self, pending):
            self.copies += 1
            return StoredObject('private/files/synthetic.pdf', 10, 'application/pdf', 'a' * 64, True)
        def delete_upload(self, _): raise RuntimeError('합성 정리 실패')
    objects = Objects()
    service = RemoteUploadVerificationService(store, objects)
    first = service.complete('upload-owner', intent.intent_id)
    db("UPDATE server_remote_upload_intents SET expires_at=CURRENT_TIMESTAMP - interval '1 second' WHERE intent_id=%s", (intent.intent_id,))
    assert service.complete('upload-owner', intent.intent_id) == first
    assert store.mark_verified('upload-owner', intent.intent_id, 'private/files/losing-copy.pdf') == first
    assert store.get_completed('other-owner', intent.intent_id) is None
    assert objects.copies == 1
    assert db('SELECT count(*) AS n FROM server_pdf_uploads WHERE paper_id=%s', (intent.paper_id,))[0]['n'] == 1


def test_expired_reservation_commits_expired_status_without_upload(pool, db):
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES ('expired-owner','합성 사용자')")
    store = PostgresRemoteUploadIntentStore(pool)
    intent = store.create('expired-owner', 'expired-key', 'paper.pdf', 10, 'application/pdf', 'b' * 64)
    db("UPDATE server_remote_upload_intents SET expires_at=CURRENT_TIMESTAMP - interval '1 second' WHERE intent_id=%s", (intent.intent_id,))
    for _ in range(2):
        with pytest.raises(ValueError, match='remote_upload_expired'):
            store.reserve_upload('expired-owner', intent.intent_id)
    assert db('SELECT status FROM server_remote_upload_intents WHERE intent_id=%s', (intent.intent_id,))[0]['status'] == 'expired'
    assert db('SELECT count(*) AS n FROM server_pdf_uploads WHERE paper_id=%s', (intent.paper_id,))[0]['n'] == 0
