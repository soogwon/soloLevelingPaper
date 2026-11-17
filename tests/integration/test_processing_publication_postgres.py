"""실제 pgvector에서 staging의 원자적 게시와 검색 연결을 검증한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import os

import pytest

from solo_leveling.workers.processing_publication import PostgresStagedProcessingPublisher, ProcessingPublishError
from solo_leveling.interfaces.http.processing_jobs import PostgresProcessingJobStore
from solo_leveling.interfaces.http.remote_vectors import PostgresEmbeddingVectorWriter, PostgresRemoteScopedSearch
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters
from solo_leveling.workers.processing_authorization import PostgresProcessingAuthorization
from tests.integration.test_remote_processing_postgres import pool, db  # noqa: F401
from tests.integration.test_processing_staging_postgres import setup

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL DSN 필요')


def prepared(pool, db):
    stage, value = setup(pool, db)
    receipt = stage.save(value)
    publisher = PostgresStagedProcessingPublisher(stage)
    return publisher, value.source, receipt.payload_sha256


def publish(publisher, source, digest):
    return publisher.publish(source, digest, expected_active_publication_id=None)


def assert_private(db, source):
    for table in ('server_parse_revisions', 'server_processing_publications'):
        assert db(f'SELECT count(*) AS n FROM {table} WHERE source_job_id=%s', (source.job_id,))[0]['n'] == 0
    assert db('SELECT status FROM server_processing_jobs WHERE job_id=%s', (source.job_id,))[0]['status'] == 'running'
    assert db('SELECT count(*) AS n FROM server_processing_result_staging WHERE job_id=%s', (source.job_id,))[0]['n'] == 1


def test_atomic_publish_replay_and_real_search(pool, db):
    publisher, source, digest = prepared(pool, db)
    assert publisher.current(source) is None
    first = publish(publisher, source, digest)
    assert publish(publisher, source, digest) == first
    assert publisher.current(source) == first.publication_id
    row = db('SELECT status,lease_token FROM server_processing_jobs WHERE job_id=%s', (source.job_id,))[0]
    assert row == dict(status='succeeded', lease_token=None)
    metadata = db('SELECT document_metadata FROM server_parse_revisions WHERE parse_revision_id=%s', (source.parse_revision_id,))[0]['document_metadata']
    assert metadata['page_count'] == 2 and metadata['empty_pages'] == [2]
    assert metadata['sections'][0]['title'] == 'Synthetic'
    assert db('SELECT printed_page_label FROM server_processing_chunks WHERE parse_revision_id=%s', (source.parse_revision_id,))[0]['printed_page_label'] == 'i'
    context = str(uuid4())
    db('''INSERT INTO server_execution_contexts (context_id,owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id)
        VALUES (%s,%s,%s,%s,%s,%s)''', (context, source.owner_user_id, source.paper_id, source.version_id, first.publication_id, source.parse_revision_id))
    resolver = PostgresRemoteContextResolver(pool)
    binding = resolver.resolve(source.owner_user_id, context)
    assert binding and resolver.resolve('another-owner', context) is None
    search = PostgresRemoteScopedSearch(pool, lambda *_: (1.0, 0.0))
    hits = search.search('합성 질문', binding, RemoteSearchFilters(), 10)
    assert len(hits) == 1 and hits[0].cosine_distance == 0
    assert search.search('합성 질문', binding, RemoteSearchFilters((2,)), 10) == ()


def test_concurrent_publish_returns_same_publication(pool, db):
    publisher, source, digest = prepared(pool, db)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: publish(publisher, source, digest), range(2)))
    assert results[0] == results[1]
    assert db('SELECT count(*) AS n FROM server_processing_publications WHERE source_job_id=%s', (source.job_id,))[0]['n'] == 1


@pytest.mark.parametrize('point', ['provenance', 'vectors'])
def test_failed_publication_rolls_back_everything(pool, db, monkeypatch, point):
    publisher, source, digest = prepared(pool, db)
    with monkeypatch.context() as patch:
        if point == 'provenance':
            def fail(*args): raise RuntimeError('비공개 본문')
            patch.setattr(publisher, '_record_provenance', fail)
        else: patch.setattr(PostgresEmbeddingVectorWriter, 'put', lambda *args: None)
        with pytest.raises(ProcessingPublishError) as error: publish(publisher, source, digest)
        assert '비공개' not in str(error.value)
    assert_private(db, source)
    assert publisher.current(source) is None
    publish(publisher, source, digest)


@pytest.mark.parametrize('change', ['digest', 'pointer', 'cancel', 'revoked', 'lease'])
def test_stale_authority_and_receipt_cannot_publish(pool, db, change):
    publisher, source, digest = prepared(pool, db)
    expected = None
    if change == 'digest': digest = 'f' * 64
    elif change == 'pointer': expected = str(uuid4())
    elif change == 'cancel': PostgresProcessingJobStore(pool).cancel(source.owner_user_id, source.job_id)
    elif change == 'lease': db("UPDATE server_processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (source.job_id,))
    else:
        intent = PostgresProcessingAuthorization(pool).dispatch_intent(source)
        db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE consent_id=%s', (intent.ai_consent_id,))
    with pytest.raises(ProcessingPublishError): publisher.publish(source, digest, expected_active_publication_id=expected)
    assert publisher.current(source) is None
    if change == 'lease': PostgresProcessingJobStore(pool).cancel(source.owner_user_id, source.job_id)


def test_completed_replay_requires_current_consent(pool, db):
    publisher, source, digest = prepared(pool, db)
    intent = PostgresProcessingAuthorization(pool).dispatch_intent(source)
    first = publish(publisher, source, digest)
    db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE consent_id=%s', (intent.ai_consent_id,))
    with pytest.raises(ProcessingPublishError): publish(publisher, source, digest)
    assert publisher.current(source) == first.publication_id


def legacy_publication(pool, source):
    """다른 실행이 같은 판본에 게시한 상황을 기존 게시 포트로 만든다."""
    from dataclasses import replace
    from tests.fixtures.processing_result import result
    from solo_leveling.workers.processing_publication import _TransactionPool
    from solo_leveling.interfaces.http.processing_publications import PostgresProcessingPublicationStore
    jobs = PostgresProcessingJobStore(pool)
    queued = jobs.enqueue(source.owner_user_id, source.paper_id, source.version_id,
                          'full_processing', str(uuid4()), 'c' * 64)
    claimed = jobs.claim('competing-worker', lease_seconds=60)
    assert claimed.job_id == queued.job_id
    other = replace(source, job_id=claimed.job_id, lease_token=claimed.lease_token,
                    parse_revision_id=str(uuid4()), translation_revision_id=str(uuid4()), embedding_set_id=str(uuid4()))
    with pool.connection() as conn:
        tx = _TransactionPool(conn)
        PostgresStagedProcessingPublisher._materialize(conn, tx, result(other))
        return PostgresProcessingPublicationStore(tx).publish(other.owner_user_id, other.paper_id, other.version_id,
            other.parse_revision_id, other.translation_revision_id, other.embedding_set_id, other.job_id, other.lease_token)


def test_pointer_conflict_and_replay_preserve_newer_publication(pool, db):
    publisher, source, digest = prepared(pool, db)
    old = legacy_publication(pool, source)
    with pytest.raises(ProcessingPublishError, match='conflict'): publish(publisher, source, digest)
    first = publisher.publish(source, digest, expected_active_publication_id=old.publication_id)
    newest = legacy_publication(pool, source)
    assert publish(publisher, source, digest) == first
    assert publisher.current(source) == newest.publication_id


def test_expiry_during_materialization_rolls_back(pool, db, monkeypatch):
    publisher, source, digest = prepared(pool, db)
    materialize = publisher._materialize
    def expire(conn, tx, result):
        materialize(conn, tx, result)
        conn.execute("UPDATE server_processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (source.job_id,))
    monkeypatch.setattr(publisher, '_materialize', expire)
    with pytest.raises(ProcessingPublishError): publish(publisher, source, digest)
    assert_private(db, source)
    assert publisher.current(source) is None
