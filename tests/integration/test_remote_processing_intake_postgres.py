"""검증된 업로드 접수의 동시성·동의·작업 원자성을 실제 DB에서 확인한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, new_id  # noqa: F401
from solo_leveling.interfaces.http.remote_uploads import PostgresRemoteUploadIntentStore, RemoteUploadVerificationService
from solo_leveling.interfaces.http.object_storage import StoredObject
from solo_leveling.interfaces.http.remote_upload_consents import (
    NOTICE_VERSION,
    PostgresRemoteUploadConsentStore,
)
from solo_leveling.interfaces.http.remote_processing_intake import PostgresRemoteProcessingIntake, RemoteProcessingConflict

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


def test_deleted_paper_hides_recovery_and_same_key_replay(pool, db):
    owner, intent, storage, ai = admitted_source(pool, db)
    intake = PostgresRemoteProcessingIntake(pool)
    first = intake.admit(owner, intent, "deleted-key", storage, ai, NOTICE_VERSION)
    assert intake.get(owner, intent) == first
    db("UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE paper_id=%s",
       (first.paper_id,))
    assert intake.get(owner, intent) is None
    assert intake.admit(owner, intent, "deleted-key", storage, ai, NOTICE_VERSION) is None
    assert db("SELECT count(*) AS n FROM server_processing_jobs WHERE paper_id=%s",
              (first.paper_id,))[0]["n"] == 1


def admitted_source(pool, db, *, pdf=None):
    from hashlib import sha256
    size = 10 if pdf is None else len(pdf)
    digest = "a" * 64 if pdf is None else sha256(pdf).hexdigest()
    db("""INSERT INTO server_dispatch_controls(control_id,generation,dispatch_enabled,intake_enabled)
          VALUES ('provider_dispatch',1,true,true)
          ON CONFLICT (control_id) DO UPDATE SET intake_enabled=true,dispatch_enabled=true""")
    owner = new_id()
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'합성 사용자')", (owner,))
    store = PostgresRemoteUploadIntentStore(pool)
    intent = store.create(owner, new_id(), 'paper.pdf', size, 'application/pdf', digest)
    consents = PostgresRemoteUploadConsentStore(pool)
    storage = consents.grant(owner, intent.intent_id, 'storage', new_id())

    class Objects:
        def inspect_upload(self, pending):
            return StoredObject(pending.object_key, size, 'application/pdf', digest, True)
        def finalize_upload(self, pending):
            # 실제 게시 adapter와 같은 owner/paper/version 경계를 보존한다.
            prefix = pending.object_key.rsplit('/', 1)[0].replace('private/tmp/', 'private/files/', 1)
            return StoredObject(prefix + '/' + new_id() + '.pdf', size, 'application/pdf', digest, True)
        def delete_upload(self, pending):
            pass

    RemoteUploadVerificationService(store, Objects()).complete(owner, intent.intent_id)
    ai = consents.grant(owner, intent.intent_id, 'ai_provider', new_id())
    return owner, intent.intent_id, storage.consent_id, ai.consent_id


@pytest.mark.parametrize('same_key', [True, False])
def test_concurrent_duplicate_upload_creates_only_one_job(pool, db, same_key):
    owner, intent, storage, ai = admitted_source(pool, db)
    intake = PostgresRemoteProcessingIntake(pool)
    keys = ['same', 'same'] if same_key else ['one', 'two']
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(
            lambda key: intake.admit(owner, intent, key, storage, ai, NOTICE_VERSION), keys))
    assert results[0] == results[1] and results[0].status == 'queued'
    assert db('SELECT count(*) AS n FROM server_processing_jobs WHERE owner_user_id=%s', (owner,))[0]['n'] == 1
    assert db('SELECT count(*) AS n FROM server_processing_job_authorizations WHERE owner_user_id=%s', (owner,))[0]['n'] == 1


def test_revoked_consent_blocks_new_work_but_replay_remains_read_only(pool, db):
    owner, intent, storage, ai = admitted_source(pool, db)
    intake = PostgresRemoteProcessingIntake(pool)
    db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE consent_id=%s', (ai,))
    assert intake.admit(owner, intent, 'key', storage, ai, NOTICE_VERSION) is None
    assert db('SELECT count(*) AS n FROM server_processing_jobs WHERE owner_user_id=%s', (owner,))[0]['n'] == 0
    db('UPDATE server_remote_consents SET revoked_at=NULL WHERE consent_id=%s', (ai,))
    first = intake.admit(owner, intent, 'key', storage, ai, NOTICE_VERSION)
    db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE consent_id=%s', (ai,))
    assert intake.admit(owner, intent, 'key', storage, ai, NOTICE_VERSION) == first
    with pytest.raises(RemoteProcessingConflict):
        intake.admit(owner, intent, 'key', storage, ai, 'other-notice')


def test_stop_preserves_replay_and_controls_change_atomically(pool, db):
    from solo_leveling.interfaces.http.remote_operation_controls import PostgresRemoteOperationControls
    owner, intent, storage, ai = admitted_source(pool, db)
    intake = PostgresRemoteProcessingIntake(pool)
    first = intake.admit(owner, intent, "key", storage, ai, NOTICE_VERSION)
    controls = PostgresRemoteOperationControls(pool)
    before = controls.get()
    stopped = controls.update(before.generation, intake_enabled=False, dispatch_enabled=True)
    assert not stopped.intake_enabled and stopped.dispatch_enabled
    assert controls.update(before.generation, intake_enabled=True, dispatch_enabled=False) is None
    assert intake.admit(owner, intent, "key", storage, ai, NOTICE_VERSION) == first
    other_owner, other_intent, other_storage, other_ai = admitted_source(pool, db)
    current = controls.get()
    controls.update(current.generation, intake_enabled=False, dispatch_enabled=True)
    assert intake.admit(
        other_owner, other_intent, "key", other_storage, other_ai, NOTICE_VERSION) is None
    assert db('SELECT count(*) AS n FROM server_processing_jobs WHERE owner_user_id=%s', (other_owner,))[0]['n'] == 0


def test_legacy_control_insert_defaults_to_intake_blocked(pool, db):
    db('DELETE FROM server_dispatch_controls')
    db("""INSERT INTO server_dispatch_controls(control_id,generation,dispatch_enabled)
          VALUES ('provider_dispatch',1,true)""")
    row = db('SELECT intake_enabled,dispatch_enabled FROM server_dispatch_controls')[0]
    assert row == {'intake_enabled': False, 'dispatch_enabled': True}
