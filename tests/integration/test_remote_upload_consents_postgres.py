"""실제 PostgreSQL에서 업로드 동의의 멱등·철회·대상 격리를 확인한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
import os

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, new_id  # noqa: F401
from solo_leveling.interfaces.http.object_storage import StoredObject
from solo_leveling.interfaces.http.remote_upload_consents import (
    NOTICE_VERSION,
    PostgresRemoteUploadConsentStore,
    RemoteUploadConsentConflict,
)
from solo_leveling.interfaces.http.remote_uploads import (
    PostgresRemoteUploadIntentStore,
    RemoteUploadVerificationService,
)


pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLO_TEST_POSTGRES_DSN"), reason="실제 PostgreSQL DSN 필요")


def source(pool, db, *, verified=False):
    owner = new_id()
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES (%s,'합성 사용자')", (owner,))
    upload = PostgresRemoteUploadIntentStore(pool)
    intent = upload.create(owner, new_id(), "paper.pdf", 10, "application/pdf", "a" * 64)
    if verified:
        class Objects:
            def inspect_upload(self, pending):
                return StoredObject(pending.object_key, 10, "application/pdf", "a" * 64, True)

            def finalize_upload(self, pending):
                return StoredObject("private/files/" + new_id() + ".pdf", 10, "application/pdf", "a" * 64, True)

            def delete_upload(self, _key):
                pass

        RemoteUploadVerificationService(upload, Objects()).complete(owner, intent.intent_id)
    return owner, intent.intent_id


def test_storage_before_upload_and_ai_after_verification_with_replay_and_regrant(pool, db):
    owner, intent = source(pool, db)
    store = PostgresRemoteUploadConsentStore(pool)
    storage = store.grant(owner, intent, "storage", "storage-key")
    assert storage.revision == 1 and storage.revoked_at is None
    assert store.grant(owner, intent, "storage", "storage-key") == storage
    assert store.grant(owner, intent, "storage", "storage-alias") == storage
    with pytest.raises(ValueError, match="remote_upload_not_verified"):
        store.grant(owner, intent, "ai_provider", "ai-key")

    upload = PostgresRemoteUploadIntentStore(pool)

    class Objects:
        def inspect_upload(self, pending):
            return StoredObject(pending.object_key, 10, "application/pdf", "a" * 64, True)

        def finalize_upload(self, pending):
            return StoredObject("private/files/" + new_id() + ".pdf", 10, "application/pdf", "a" * 64, True)

        def delete_upload(self, _key):
            pass

    RemoteUploadVerificationService(upload, Objects()).complete(owner, intent)
    ai = store.grant(owner, intent, "ai_provider", "ai-key")
    assert ai.revision == 1 and ai.notice_version == NOTICE_VERSION

    revoked = store.revoke(owner, intent, "ai_provider", 1)
    assert revoked.revoked_at is not None
    assert store.revoke(owner, intent, "ai_provider", 1) == revoked
    current = store.grant(owner, intent, "ai_provider", "ai-key-2")
    assert current.revision == 2 and current.revoked_at is None
    states = {item.purpose: item for item in store.list(owner, intent)}
    assert states == {"storage": storage, "ai_provider": current}


def test_request_key_cannot_cross_intents_and_foreign_owner_sees_missing(pool, db):
    owner, first = source(pool, db)
    upload = PostgresRemoteUploadIntentStore(pool)
    second = upload.create(owner, new_id(), "other.pdf", 11, "application/pdf", "b" * 64)
    store = PostgresRemoteUploadConsentStore(pool)
    store.grant(owner, first, "storage", "shared-key")
    with pytest.raises(RemoteUploadConsentConflict, match="remote_upload_consent_request_conflict"):
        store.grant(owner, second.intent_id, "storage", "shared-key")
    assert store.list("other-owner", first) is None
    assert store.revoke("other-owner", first, "storage", 1) is None


def test_concurrent_grants_create_one_current_consent_and_bind_both_keys(pool, db):
    owner, intent = source(pool, db)
    store = PostgresRemoteUploadConsentStore(pool)
    with ThreadPoolExecutor(max_workers=2) as workers:
        values = list(workers.map(
            lambda key: store.grant(owner, intent, "storage", key), ("one", "two")))
    assert values[0].consent_id == values[1].consent_id
    assert db(
        """SELECT count(*) AS n FROM server_remote_upload_consent_bindings
           WHERE owner_user_id=%s AND upload_intent_id=%s AND revoked_at IS NULL""",
        (owner, intent),
    )[0]["n"] == 1
    assert db(
        """SELECT count(*) AS n FROM server_remote_upload_consent_requests
           WHERE owner_user_id=%s AND upload_intent_id=%s""",
        (owner, intent),
    )[0]["n"] == 2


def test_new_notice_reconsent_revokes_old_and_creates_new_revision(pool, db):
    owner, intent = source(pool, db)
    store = PostgresRemoteUploadConsentStore(pool)
    old = store.grant(owner, intent, "storage", "old-key", "old-notice")
    new = store.grant(owner, intent, "storage", "new-key", NOTICE_VERSION)
    assert old.revision == 1 and new.revision == 2
    rows = db(
        """SELECT revision,notice_version,revoked_at FROM server_remote_upload_consent_bindings
           WHERE owner_user_id=%s AND upload_intent_id=%s AND purpose='storage'
           ORDER BY revision""",
        (owner, intent),
    )
    assert rows[0]["notice_version"] == "old-notice" and rows[0]["revoked_at"] is not None
    assert rows[1]["notice_version"] == NOTICE_VERSION and rows[1]["revoked_at"] is None
