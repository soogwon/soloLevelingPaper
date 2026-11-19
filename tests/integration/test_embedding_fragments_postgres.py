"""실제 pgvector에서 하위 조각의 격리·완전성·부모 집계를 확인한다."""
# ruff: noqa: F811, F401
from dataclasses import replace
import json
import os

import pytest

from tests.integration.test_remote_processing_postgres import (
    pool, db, stores, make_paper, claimed_job, make_revision_set, mark_ready, publish, new_id,
)  # noqa: F401
from tests.unit.test_embedding_token_windows import Tokenizer
from solo_leveling.infrastructure.embeddings.token_windows import split_token_windows, TokenWindowPolicy
from solo_leveling.interfaces.http.remote_fragment_vectors import PostgresEmbeddingFragmentWriter
from solo_leveling.interfaces.http.remote_vectors import EmbeddingWriteScope, PostgresRemoteScopedSearch
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def seed(db, stores, pool, *, owner='fragment-owner', paper=None):
    paper = paper or make_paper(db, owner)
    job = claimed_job(stores[0], owner, paper)
    revision = make_revision_set(db, owner, paper, job, chunks=2, ready=False)
    parse, translation, embedding, ids = revision
    fp = dict(dimension=2, max_length=128, split_policy=TokenWindowPolicy().fingerprint())
    db('UPDATE server_embedding_sets SET model_fingerprint=%s::jsonb WHERE embedding_set_id=%s',
       (json.dumps(fp), embedding))
    scope = EmbeddingWriteScope(owner, paper, 'v1', parse, translation, embedding, 2)
    fragments = [split_token_windows(Tokenizer(), '248', parent_chunk_id=c,
                 embedding_set_id=embedding, max_length=128) for c in ids]
    writer = PostgresEmbeddingFragmentWriter(pool)
    for index, group in enumerate(fragments):
        db('UPDATE server_processing_chunks SET pdf_page=%s WHERE chunk_id=%s', (index+1, ids[index]))
        for fragment in group:
            writer.put(scope, fragment, 248, (1.,0.) if index == 0 else (0.8,0.6))
    return owner, paper, job, revision, scope, fragments


def finish(db, stores, pool, state):
    owner, paper, job, revision, *_ = state
    parse, translation, embedding, _ = revision
    mark_ready(db, parse, translation, embedding)
    published = publish(stores[1], owner, paper, revision, job)
    context = new_id()
    db('''INSERT INTO server_execution_contexts
        (context_id,owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id)
        VALUES (%s,%s,%s,'v1',%s,%s)''', (context,owner,paper,published.publication_id,parse))
    return PostgresRemoteContextResolver(pool).resolve(owner, context)


def test_fragments_group_before_limit_and_keep_original_chunk_ids(db, stores, pool):
    state = seed(db, stores, pool)
    binding = finish(db, stores, pool, state)
    search = PostgresRemoteScopedSearch(pool, lambda *_: (1.,0.))
    hits = search.search('합성', binding, RemoteSearchFilters(), 2)
    assert [h.chunk_id for h in hits] == state[3][3]
    assert [h.cosine_distance for h in hits] == pytest.approx([0.,0.2])
    assert [h.chunk_id for h in search.search('합성', binding, RemoteSearchFilters((2,)), 1)] == [state[3][3][1]]
    assert search.search('합성', replace(binding, owner_user_id='other'), RemoteSearchFilters(), 2) == ()


@pytest.mark.parametrize('fault', ['gap', 'tail', 'parent-count', 'policy'])
def test_incomplete_mapping_cannot_become_ready(db, stores, pool, fault):
    import psycopg
    state = seed(db, stores, pool)
    owner, _, _, revision, _, fragments = state
    first = fragments[0]
    if fault in ('gap','tail'):
        target = first[1] if fault == 'gap' else first[-1]
        db('DELETE FROM server_embedding_fragments WHERE owner_user_id=%s AND embedding_fragment_id=%s',
           (owner,target.embedding_fragment_id))
    elif fault == 'parent-count':
        db('UPDATE server_embedding_sets SET indexed_chunk_count=1 WHERE embedding_set_id=%s', (revision[2],))
    else:
        db("UPDATE server_embedding_sets SET model_fingerprint=jsonb_set(model_fingerprint,'{split_policy,stride_tokens}','0') WHERE embedding_set_id=%s", (revision[2],))
    with pytest.raises(psycopg.errors.CheckViolation):
        mark_ready(db, *revision[:3])
    assert db('SELECT active_publication_id FROM server_processing_versions WHERE paper_id=%s', (state[1],))[0]['active_publication_id'] is None


def test_replay_conflict_and_finalized_mutation(db, stores, pool):
    import psycopg
    state = seed(db, stores, pool)
    writer = PostgresEmbeddingFragmentWriter(pool)
    scope, fragment = state[4], state[5][0][0]
    writer.put(scope, fragment, 248, (1.,0.))
    with pytest.raises(ValueError, match='target_invalid'):
        writer.put(scope, fragment, 248, (0.,1.))
    finish(db, stores, pool, state)
    with pytest.raises(psycopg.errors.CheckViolation):
        db('DELETE FROM server_embedding_fragments WHERE embedding_set_id=%s', (scope.embedding_set_id,))


def test_new_set_does_not_change_old_context(db, stores, pool):
    old_state = seed(db, stores, pool)
    old = finish(db, stores, pool, old_state)
    new_state = seed(db, stores, pool, paper=old.paper_id)
    new = finish(db, stores, pool, new_state)
    assert new.embedding_set_id != old.embedding_set_id
    assert PostgresRemoteContextResolver(pool).resolve(old.owner_user_id, old.context_id) == old
    hits = PostgresRemoteScopedSearch(pool, lambda *_: (1.,0.)).search('합성', old, RemoteSearchFilters(), 2)
    assert [h.chunk_id for h in hits] == old_state[3][3]


def test_expired_worker_cannot_add_fragment(db, stores, pool):
    import psycopg
    state = seed(db, stores, pool)
    db("UPDATE server_processing_jobs SET lease_expires_at=CURRENT_TIMESTAMP-interval '1 second' WHERE job_id=%s",
       (state[2].job_id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        PostgresEmbeddingFragmentWriter(pool).put(state[4], state[5][0][0], 248, (1.,0.))
    db("UPDATE server_processing_jobs SET lease_expires_at=CURRENT_TIMESTAMP+interval '1 hour' WHERE job_id=%s",
       (state[2].job_id,))


def test_paper_deletion_removes_fragments_and_old_binding_returns_nothing(db, stores, pool):
    state = seed(db, stores, pool)
    binding = finish(db, stores, pool, state)
    db('DELETE FROM server_papers WHERE paper_id=%s', (state[1],))
    assert db('SELECT count(*) AS n FROM server_embedding_fragments WHERE paper_id=%s', (state[1],))[0]['n'] == 0
    assert PostgresRemoteScopedSearch(pool, lambda *_: (1.,0.)).search('합성', binding, RemoteSearchFilters(), 2) == ()
