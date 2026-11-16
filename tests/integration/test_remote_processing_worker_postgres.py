"""claim한 원격 작업의 owner·lease·업로드·동의 결합을 실제 PostgreSQL에서 검증한다."""
# ruff: noqa: F811
import os

import pytest

from solo_leveling.interfaces.http.processing_jobs import PostgresProcessingJobStore
from solo_leveling.interfaces.http.remote_processing_intake import PostgresRemoteProcessingIntake
from solo_leveling.interfaces.http.remote_upload_consents import NOTICE_VERSION
from solo_leveling.workers.remote_processing import (
    PostgresRemoteProcessingInputStore,
    RemoteProcessingInputRejected,
)
from tests.integration.test_remote_processing_intake_postgres import admitted_source
from tests.integration.test_remote_processing_postgres import db, new_id, pool  # noqa: F401


pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLO_TEST_POSTGRES_DSN"), reason="실제 PostgreSQL DSN 필요"
)


def claimed_source(pool, db):
    owner, intent, storage, ai = admitted_source(pool, db)
    admitted = PostgresRemoteProcessingIntake(pool).admit(
        owner, intent, new_id(), storage, ai, NOTICE_VERSION,
    )
    claimed = PostgresProcessingJobStore(pool).claim("worker-517", lease_seconds=60)
    assert admitted.job_id == claimed.job_id
    return claimed, intent, storage, ai


def test_claimed_generation_loads_only_verified_remote_source_and_current_consents(pool, db):
    claimed, intent, _storage, ai = claimed_source(pool, db)
    store = PostgresRemoteProcessingInputStore(pool)

    loaded = store.load(claimed)
    assert loaded.source.owner_user_id == claimed.owner_user_id
    assert loaded.source.job_id == claimed.job_id
    assert loaded.source.lease_token == claimed.lease_token
    assert loaded.object_key.startswith("private/files/")
    assert store.allowed(loaded.source, "parse")
    assert store.allowed(loaded.source, "translation")

    db(
        """UPDATE server_remote_upload_consent_bindings SET revoked_at=CURRENT_TIMESTAMP
           WHERE owner_user_id=%s AND upload_intent_id=%s AND consent_id=%s""",
        (claimed.owner_user_id, intent, ai),
    )
    db(
        """UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP
           WHERE owner_user_id=%s AND consent_id=%s""",
        (claimed.owner_user_id, ai),
    )
    assert not store.allowed(loaded.source, "translation")


def test_reclaimed_job_rejects_previous_worker_generation(pool, db):
    claimed, _intent, _storage, _ai = claimed_source(pool, db)
    store = PostgresRemoteProcessingInputStore(pool)
    previous = store.load(claimed).source
    db(
        """UPDATE server_processing_jobs SET lease_expires_at=CURRENT_TIMESTAMP-interval '1 second'
           WHERE owner_user_id=%s AND job_id=%s""",
        (claimed.owner_user_id, claimed.job_id),
    )
    replacement = PostgresProcessingJobStore(pool).claim("worker-replacement", lease_seconds=60)
    assert replacement.job_id == claimed.job_id and replacement.lease_token != claimed.lease_token
    assert not store.allowed(previous, "parse")
    with pytest.raises(RemoteProcessingInputRejected):
        store.load(claimed)
