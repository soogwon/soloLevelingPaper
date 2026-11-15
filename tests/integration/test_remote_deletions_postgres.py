"""삭제 표식의 조회 차단과 쓰기 경합을 실제 PostgreSQL에서 확인한다."""
# ruff: noqa: F811
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from tests.integration.test_remote_processing_postgres import (  # noqa: F401
    pool, db, stores, make_paper, claimed_job, make_revision_set, mark_ready, publish, new_id,
)
from solo_leveling.interfaces.http.remote_deletions import PostgresRemoteDeletionStore
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.interfaces.http.remote_vectors import EmbeddingWriteScope, PostgresEmbeddingVectorWriter, PostgresRemoteScopedSearch
from solo_leveling.interfaces.http.processing_publications import ProcessingPublicationNotFound
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='실제 PostgreSQL DSN 필요')


def test_tombstone_hides_existing_context_search_and_publication(db, stores, pool):
    owner = 'deletion-reader'
    paper = make_paper(db, owner)
    job = claimed_job(stores[0], owner, paper)
    revision = make_revision_set(db, owner, paper, job, chunks=1, ready=False)
    parse, translation, embedding, ids = revision
    db('UPDATE server_embedding_sets SET model_fingerprint=%s::jsonb WHERE embedding_set_id=%s',
       (json.dumps({'dimension': 2}), embedding))
    PostgresEmbeddingVectorWriter(pool).put(EmbeddingWriteScope(owner, paper, 'v1', parse, translation, embedding, 2), ids[0], (1., 0.))
    mark_ready(db, parse, translation, embedding)
    publication = publish(stores[1], owner, paper, revision, job)
    context = new_id()
    db("INSERT INTO server_execution_contexts(context_id,owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id) VALUES (%s,%s,%s,'v1',%s,%s)",
       (context, owner, paper, publication.publication_id, parse))
    resolver = PostgresRemoteContextResolver(pool)
    binding = resolver.resolve(owner, context)
    search = PostgresRemoteScopedSearch(pool, lambda *_: (1., 0.))
    assert len(search.search('synthetic', binding, RemoteSearchFilters(), 5)) == 1
    PostgresRemoteDeletionStore(pool).request(owner, paper, new_id())
    assert resolver.resolve(owner, context) is None
    assert search.search('synthetic', binding, RemoteSearchFilters(), 5) == ()
    with pytest.raises(ProcessingPublicationNotFound):
        stores[1].get_active(owner, paper, 'v1')
    with pytest.raises(ProcessingPublicationNotFound):
        publish(stores[1], owner, paper, revision, job)


def test_existing_paper_request_does_not_hide_conflicting_request_key(db, pool):
    owner = 'deletion-key'
    first, second = make_paper(db, owner), make_paper(db, owner)
    store = PostgresRemoteDeletionStore(pool)
    store.request(owner, first, 'key-one')
    store.request(owner, second, 'key-two')
    with pytest.raises(ValueError, match='remote_deletion_request_conflict'):
        store.request(owner, second, 'key-one')


def test_write_waits_for_uncommitted_tombstone_then_rejects(db, pool):
    import psycopg
    owner = 'deletion-race'
    paper = make_paper(db, owner)
    started = Event()
    def write_version():
        started.set()
        db("INSERT INTO server_processing_versions(owner_user_id,paper_id,server_version_id) VALUES (%s,%s,'late')", (owner, paper))
    with ThreadPoolExecutor(max_workers=1) as executor:
        with pool.connection() as conn:
            conn.execute('UPDATE server_papers SET deleted_at=CURRENT_TIMESTAMP WHERE paper_id=%s', (paper,))
            future = executor.submit(write_version)
            assert started.wait(5)
            # 잠금이 풀리기 전 성공해 버리면 삭제와 후발 쓰기의 경계가 깨진 것이다.
            from concurrent.futures import TimeoutError
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
        with pytest.raises(psycopg.errors.CheckViolation, match='paper_tombstoned'):
            future.result(timeout=5)
    assert db("SELECT count(*) AS n FROM server_processing_versions WHERE paper_id=%s AND server_version_id='late'", (paper,))[0]['n'] == 0
