"""검증된 업로드 접수의 동시성·동의·작업 원자성을 실제 DB에서 확인한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, new_id  # noqa: F401
from solo_leveling.interfaces.http.remote_uploads import PostgresRemoteUploadIntentStore, RemoteUploadVerificationService
from solo_leveling.interfaces.http.object_storage import StoredObject
from solo_leveling.interfaces.http.remote_processing_intake import PostgresRemoteProcessingIntake, RemoteProcessingConflict

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


def admitted_source(pool, db):
    owner = new_id()
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'합성 사용자')", (owner,))
    store = PostgresRemoteUploadIntentStore(pool)
    intent = store.create(owner, new_id(), 'paper.pdf', 10, 'application/pdf', 'a' * 64)

    class Objects:
        def inspect_upload(self, pending):
            return StoredObject(pending.object_key, 10, 'application/pdf', 'a' * 64, True)
        def finalize_upload(self, pending):
            return StoredObject('private/files/' + new_id() + '.pdf', 10, 'application/pdf', 'a' * 64, True)
        def delete_upload(self, pending):
            pass

    RemoteUploadVerificationService(store, Objects()).complete(owner, intent.intent_id)
    payload = db('SELECT payload_hash FROM server_remote_upload_intents WHERE intent_id=%s', (intent.intent_id,))[0]['payload_hash']
    storage, ai = new_id(), new_id()
    db('''INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version)
          VALUES (%s,%s,'storage',%s,'notice'),(%s,%s,'ai_provider',%s,'notice')''',
       (storage, owner, payload, ai, owner, payload))
    return owner, intent.intent_id, storage, ai


@pytest.mark.parametrize('same_key', [True, False])
def test_concurrent_duplicate_upload_creates_only_one_job(pool, db, same_key):
    owner, intent, storage, ai = admitted_source(pool, db)
    intake = PostgresRemoteProcessingIntake(pool)
    keys = ['same', 'same'] if same_key else ['one', 'two']
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda key: intake.admit(owner, intent, key, storage, ai, 'notice'), keys))
    assert results[0] == results[1] and results[0].status == 'queued'
    assert db('SELECT count(*) AS n FROM server_processing_jobs WHERE owner_user_id=%s', (owner,))[0]['n'] == 1
    assert db('SELECT count(*) AS n FROM server_processing_job_authorizations WHERE owner_user_id=%s', (owner,))[0]['n'] == 1


def test_revoked_consent_blocks_new_work_but_replay_remains_read_only(pool, db):
    owner, intent, storage, ai = admitted_source(pool, db)
    intake = PostgresRemoteProcessingIntake(pool)
    db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE consent_id=%s', (ai,))
    assert intake.admit(owner, intent, 'key', storage, ai, 'notice') is None
    assert db('SELECT count(*) AS n FROM server_processing_jobs WHERE owner_user_id=%s', (owner,))[0]['n'] == 0
    db('UPDATE server_remote_consents SET revoked_at=NULL WHERE consent_id=%s', (ai,))
    first = intake.admit(owner, intent, 'key', storage, ai, 'notice')
    db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE consent_id=%s', (ai,))
    assert intake.admit(owner, intent, 'key', storage, ai, 'notice') == first
    with pytest.raises(RemoteProcessingConflict):
        intake.admit(owner, intent, 'key', storage, ai, 'other-notice')
