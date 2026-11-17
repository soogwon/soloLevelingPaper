"""격리 PostgreSQL에서 staging 불변·세대·동의·삭제·원자성을 검증한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import os

import pytest

from solo_leveling.interfaces.http.processing_jobs import PostgresProcessingJobStore
from solo_leveling.workers.processing_authorization import PostgresProcessingAuthorization
from solo_leveling.workers.processing_staging import PostgresProcessingResultStaging, ProcessingStagingError
from tests.fixtures.processing_result import result, LIMITS, SETTINGS, SPEC
from tests.integration.test_remote_processing_postgres import pool, db  # noqa: F401
from tests.integration.test_remote_processing_worker_postgres import claimed_source
from tests.integration.test_processing_authorization_postgres import setup_authorization

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL DSN 필요')


def setup(pool, db):
    job, _, _, _ = claimed_source(pool, db)
    source = PostgresProcessingAuthorization(pool).load(job).source
    return make_store(pool), result(source)


def make_store(pool):
    return PostgresProcessingResultStaging(pool, limits=LIMITS, translation_settings=SETTINGS,
                                          embedding_spec=SPEC, max_result_bytes=100_000)


def count(db, value):
    return db('SELECT count(*) AS n FROM server_processing_result_staging WHERE job_id=%s',
              (value.source.job_id,))[0]['n']


def test_roundtrip_replay_and_no_publication(pool, db):
    store, value = setup(pool, db)
    first = store.save(value)
    loaded = make_store(pool).load(value.source)
    assert loaded['result']['source'] == asdict(value.source)
    intent = PostgresProcessingAuthorization(pool).dispatch_intent(value.source)
    assert loaded['authorization'] == dict(input_hash=intent.input_hash, storage_consent_id=intent.storage_consent_id,
        ai_consent_id=intent.ai_consent_id, notice_version=intent.notice_version)
    assert loaded['result']['document']['chunks'][0]['original_text'] == 'Synthetic text.'
    assert loaded['result']['translations'][0]['text'] == '합성 문장.'
    assert loaded['result']['document']['sections'][0]['title'] == 'Synthetic'
    assert loaded['result']['embeddings']['vectors'] == [[1.0, 0.0]]
    second = make_store(pool).save(value)
    assert not first.replayed and second.replayed and first.payload_sha256 == second.payload_sha256
    assert count(db, value) == 1
    assert db('SELECT status FROM server_processing_jobs WHERE job_id=%s', (value.source.job_id,))[0]['status'] == 'running'
    assert db('SELECT active_publication_id FROM server_processing_versions WHERE paper_id=%s',
              (value.source.paper_id,))[0]['active_publication_id'] is None
    assert db('SELECT count(*) AS n FROM server_parse_revisions WHERE source_job_id=%s',
              (value.source.job_id,))[0]['n'] == 0


def test_conflicting_result_and_concurrent_replay(pool, db):
    store, value = setup(pool, db)
    with ThreadPoolExecutor(max_workers=2) as executor:
        saved = list(executor.map(lambda _: store.save(value), range(2)))
    assert sorted(s.replayed for s in saved) == [False, True]
    changed = replace(value, translations=(replace(value.translations[0], text='다른 결과'),))
    with pytest.raises(ProcessingStagingError, match='conflict'): store.save(changed)
    assert count(db, value) == 1
    assert store.load(value.source)['result']['translations'][0]['text'] == '합성 문장.'


@pytest.mark.parametrize('change', ['cancel', 'deleted', 'revoked', 'lease', 'version', 'hash', 'owner'])
def test_changed_authority_rejects_save_and_load(pool, db, change):
    store, value = setup(pool, db)
    source = value.source
    if change == 'cancel': PostgresProcessingJobStore(pool).cancel(source.owner_user_id, source.job_id)
    elif change == 'deleted': db('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE paper_id=%s', (source.paper_id,))
    elif change == 'revoked':
        intent = PostgresProcessingAuthorization(pool).dispatch_intent(source)
        db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE owner_user_id=%s AND consent_id=%s',
           (source.owner_user_id, intent.ai_consent_id))
    elif change == 'lease':
        db("UPDATE server_processing_jobs SET lease_expires_at=CURRENT_TIMESTAMP-interval '1 second' WHERE job_id=%s", (source.job_id,))
    elif change == 'version': source = replace(source, version_id='other')
    elif change == 'hash': source = replace(source, pdf_sha256='f' * 64)
    else: source = replace(source, owner_user_id='other')
    with pytest.raises(ProcessingStagingError): store.save(replace(value, source=source))
    with pytest.raises(ProcessingStagingError): store.load(source)
    assert count(db, value) == 0
    if change == 'lease': PostgresProcessingJobStore(pool).cancel(value.source.owner_user_id, value.source.job_id)


@pytest.mark.parametrize('state', ['reserved', 'unknown', 'overrun'])
def test_usage_blocks_staging(pool, db, state):
    from solo_leveling.application.provider_dispatch import AttemptRequest
    _, source, ledger, intent = setup_authorization(pool, db)
    permit = ledger.authorize(AttemptRequest(intent, 'slot', 'translation', 100, 50))
    assert permit
    if state == 'unknown': ledger.unknown(permit.attempt_id)
    if state == 'overrun': ledger.observe(permit.attempt_id, 101, 50)
    value = result(source)
    with pytest.raises(ProcessingStagingError): make_store(pool).save(value)
    assert count(db, value) == 0


def test_late_worker_and_cleanup_preserve_other_paper(pool, db):
    from solo_leveling.infrastructure.storage.remote_deletion_backend import PostgresObjectDeletionBackend
    store, value = setup(pool, db)
    store.save(value)
    db("UPDATE server_processing_jobs SET lease_expires_at=CURRENT_TIMESTAMP-interval '1 second' WHERE job_id=%s", (value.source.job_id,))
    next_job = PostgresProcessingJobStore(pool).claim('replacement', lease_seconds=60)
    assert next_job.job_id == value.source.job_id
    with pytest.raises(ProcessingStagingError): store.save(value)
    with pytest.raises(ProcessingStagingError): store.load(value.source)
    newer = result(PostgresProcessingAuthorization(pool).load(next_job).source)
    store.save(newer)
    other_store, other = setup(pool, db)
    other_store.save(other)
    PostgresProcessingJobStore(pool).cancel(newer.source.owner_user_id, newer.source.job_id)
    db('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE paper_id=%s', (newer.source.paper_id,))
    PostgresObjectDeletionBackend(pool, None).delete_derived(newer.source.owner_user_id, newer.source.paper_id)
    assert count(db, value) == 0 and count(db, other) == 1


def test_failure_after_insert_rolls_back_and_sql_cannot_mutate(pool, db, monkeypatch):
    import psycopg
    store, value = setup(pool, db)
    def fail(*args): raise RuntimeError('private DB payload')
    with monkeypatch.context() as scoped:
        scoped.setattr(store, '_live', fail)
        with pytest.raises(ProcessingStagingError, match='unavailable'): store.save(value)
    assert count(db, value) == 0
    store.save(value)
    with pytest.raises(psycopg.errors.CheckViolation):
        db("UPDATE server_processing_result_staging SET payload='{}'::jsonb WHERE job_id=%s", (value.source.job_id,))


def test_expiry_at_final_check_discards_insert(pool, db, monkeypatch):
    store, value = setup(pool, db)
    live = store._live
    def expire(conn, source):
        conn.execute("UPDATE server_processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                     (source.job_id,))
        live(conn, source)
    monkeypatch.setattr(store, '_live', expire)
    with pytest.raises(ProcessingStagingError, match='not_allowed'): store.save(value)
    assert count(db, value) == 0


@pytest.mark.parametrize('change', ['revoked', 'cancel', 'deleted', 'revision', 'translation-settings', 'model'])
def test_saved_result_is_not_returned_after_authority_or_config_changes(pool, db, change):
    store, value = setup(pool, db)
    store.save(value)
    source = value.source
    if change == 'revoked':
        intent = PostgresProcessingAuthorization(pool).dispatch_intent(source)
        db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE owner_user_id=%s AND consent_id=%s',
           (source.owner_user_id, intent.storage_consent_id))
    elif change == 'cancel': PostgresProcessingJobStore(pool).cancel(source.owner_user_id, source.job_id)
    elif change == 'deleted': db('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE paper_id=%s', (source.paper_id,))
    elif change == 'revision': source = replace(source, parse_revision_id=source.job_id)
    else:
        store = PostgresProcessingResultStaging(pool, limits=LIMITS, max_result_bytes=100_000,
            translation_settings=replace(SETTINGS, prompt_version='new') if change == 'translation-settings' else SETTINGS,
            embedding_spec=replace(SPEC, runtime_version='new') if change == 'model' else SPEC)
    with pytest.raises(ProcessingStagingError): store.load(source)
    assert count(db, value) == 1


def test_rebinding_current_notice_does_not_reauthorize_existing_result(pool, db):
    store, value = setup(pool, db)
    store.save(value)
    owner = value.source.owner_user_id
    for table in ('server_processing_job_authorizations', 'server_remote_upload_consent_bindings', 'server_remote_consents'):
        db(f"UPDATE {table} SET notice_version='synthetic-new-notice' WHERE owner_user_id=%s", (owner,))
    assert PostgresProcessingAuthorization(pool).allowed(value.source, 'result')
    with pytest.raises(ProcessingStagingError): store.load(value.source)
    with pytest.raises(ProcessingStagingError, match='conflict'): store.save(value)
    assert count(db, value) == 1
