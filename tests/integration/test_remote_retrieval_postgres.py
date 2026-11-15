"""#397 저장·검색 구간만 실제 PostgreSQL+pgvector로 검증한다. 호스트 E2E는 아니다."""
# ruff: noqa: F811
# pytest의 공유 fixture import와 동명 주입 인자는 의도된 구조다.

import json
import os

import pytest

from tests.integration.test_remote_processing_postgres import (  # noqa: F401
    pool, db, stores, make_paper, claimed_job, make_revision_set, mark_ready, publish, new_id,
)
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters, validate_remote_hits
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.interfaces.http.remote_vectors import (
    EmbeddingWriteScope, PostgresEmbeddingVectorWriter, PostgresRemoteScopedSearch,
)

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),
                                reason='실제 PostgreSQL+pgvector 테스트 DSN 필요')


def seed(db, stores, pool, owner, *, paper=None, size=4):
    jobs, publications = stores
    paper = paper or make_paper(db, owner)
    job = claimed_job(jobs, owner, paper)
    assert job is not None and job.owner_user_id == owner and job.paper_id == paper
    revision = make_revision_set(db, owner, paper, job, chunks=size, ready=False)
    parse, translation, embedding, ids = revision
    db('UPDATE server_embedding_sets SET model_fingerprint=%s::jsonb WHERE embedding_set_id=%s',
       (json.dumps({'dimension': 2, 'distance': 'cosine', 'normalization': 'l2', 'model_id': 'synthetic'}), embedding))
    vectors = [(0.8, 0.6), (1., 0.), (1., 0.), (0.6, 0.8)]
    writer = PostgresEmbeddingVectorWriter(pool)
    scope = EmbeddingWriteScope(owner, paper, 'v1', parse, translation, embedding, 2)
    for i, chunk_id in enumerate(ids):
        db('UPDATE server_processing_chunks SET pdf_page=%s, section_id=%s WHERE chunk_id=%s',
           (7 if i == 2 else 6, 'outside' if i == 1 else 's4', chunk_id))
        writer.put(scope, chunk_id, vectors[i])
    mark_ready(db, parse, translation, embedding)
    publication = publish(publications, owner, paper, revision, job)
    context = new_id()
    db('''INSERT INTO server_execution_contexts
          (context_id, owner_user_id, paper_id, server_version_id, publication_id, parse_revision_id)
          VALUES (%s,%s,%s,'v1',%s,%s)''', (context, owner, paper, publication.publication_id, parse))
    binding = PostgresRemoteContextResolver(pool).resolve(owner, context)
    return paper, revision, binding


def test_actual_prefilter_preserves_allowed_evidence_before_top_k(db, stores, pool):
    _, revision, binding = seed(db, stores, pool, 'alice')
    seed(db, stores, pool, 'bob')
    filters = RemoteSearchFilters((6,), ('s4',))
    # 다른 owner, 페이지, 섹션의 완전 일치 벡터가 있어도 허용 근거가 남아야 한다.
    hits = PostgresRemoteScopedSearch(pool, lambda *_: (1., 0.)).search('synthetic', binding, filters, 1)
    result = validate_remote_hits(binding, filters, hits, 1)
    assert [r.hit.chunk_id for r in result] == [revision[3][0]]


def test_republication_does_not_change_old_context_or_old_source(db, stores, pool):
    paper, old_revision, old = seed(db, stores, pool, 'alice')
    before = db('SELECT original_text FROM server_processing_chunks WHERE chunk_id=%s', (old_revision[3][0],))
    _, new_revision, new = seed(db, stores, pool, 'alice', paper=paper)
    assert new.publication_id != old.publication_id
    assert stores[1].get_active('alice', paper, 'v1').publication_id == new.publication_id
    restored = PostgresRemoteContextResolver(pool).resolve('alice', old.context_id)
    assert restored == old
    hits = PostgresRemoteScopedSearch(pool, lambda *_: (1., 0.)).search('synthetic', restored,
                                                                 RemoteSearchFilters((6,), ('s4',)), 5)
    assert {h.chunk_id for h in hits} == {old_revision[3][0], old_revision[3][3]}
    assert not {h.chunk_id for h in hits} & set(new_revision[3])
    assert db('SELECT original_text FROM server_processing_chunks WHERE chunk_id=%s', (old_revision[3][0],)) == before


def test_foreign_context_and_missing_are_same_none(db, stores, pool):
    _, _, binding = seed(db, stores, pool, 'alice')
    resolver = PostgresRemoteContextResolver(pool)
    assert resolver.resolve('bob', binding.context_id) is None
    assert resolver.resolve('bob', new_id()) is None


def test_ready_vectors_reject_direct_mutation(db, stores, pool):
    import psycopg
    _, revision, binding = seed(db, stores, pool, 'alice')
    with pytest.raises(psycopg.errors.CheckViolation, match='processing_revision_finalized'):
        db("UPDATE server_embedding_vectors SET embedding='[1,0]'::vector WHERE embedding_set_id=%s",
           (binding.embedding_set_id,))
    count = db('SELECT count(*) AS n FROM server_embedding_vectors WHERE embedding_set_id=%s',
               (binding.embedding_set_id,))[0]['n']
    assert count == len(revision[3])


def test_deleted_paper_cannot_be_resolved_or_searched_with_old_binding(db, stores, pool):
    paper, _, binding = seed(db, stores, pool, 'alice')
    db('DELETE FROM server_papers WHERE paper_id=%s', (paper,))
    assert PostgresRemoteContextResolver(pool).resolve('alice', binding.context_id) is None
    assert PostgresRemoteScopedSearch(pool, lambda *_: (1., 0.)).search(
        'synthetic', binding, RemoteSearchFilters(), 5) == ()
    for table in ('server_processing_jobs', 'server_processing_publications', 'server_embedding_vectors'):
        assert db(f'SELECT count(*) AS n FROM {table} WHERE paper_id=%s', (paper,))[0]['n'] == 0
