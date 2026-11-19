"""분할 결과의 staging 복구와 원자 게시를 실제 DB에서 검증한다."""
# ruff: noqa: F401, F811
from dataclasses import replace
import os
from uuid import uuid4

import pytest

from tests.integration.test_remote_processing_postgres import pool, db
from tests.integration.test_processing_staging_postgres import setup, make_store
from tests.integration.test_worker_permissions_postgres import restricted
from tests.unit.test_embedding_token_windows import Tokenizer
from solo_leveling.infrastructure.embeddings.token_windows import split_token_windows, TokenWindowPolicy
from solo_leveling.domain.remote_processing import EmbeddingBatch, EmbeddingFragment
from solo_leveling.workers.processing_staging import ProcessingStagingError
from solo_leveling.workers.processing_publication import PostgresStagedProcessingPublisher
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.interfaces.http.remote_vectors import PostgresRemoteScopedSearch
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def fragmented(value):
    chunk = value.document.chunks[0]
    windows = split_token_windows(Tokenizer(), '248', parent_chunk_id=chunk.chunk_id,
        embedding_set_id=value.source.embedding_set_id,max_length=128)
    fragments = tuple(EmbeddingFragment(w.embedding_fragment_id,w.embedding_set_id,w.parent_chunk_id,
                                       w.token_start,w.token_end,248) for w in windows)
    return replace(value, embeddings=EmbeddingBatch(value.embeddings.spec,(chunk.chunk_id,),
        ((1.,0.),(0.,1.),(1.,0.)),fragments,TokenWindowPolicy().fingerprint()))


@pytest.mark.parametrize('restricted_runtime',[False,True])
def test_fragment_staging_roundtrip_atomic_publish_and_parent_search(pool, db, restricted, restricted_runtime):
    store, value = setup(pool, db)
    if restricted_runtime:
        store = make_store(restricted[0])
    value = fragmented(value)
    receipt = store.save(value)
    assert store.save(value).replayed
    loaded = store.load(value.source)
    assert loaded['schema_version'] == 3
    assert store.restore(loaded) == value
    publisher = PostgresStagedProcessingPublisher(store)
    result = publisher.publish(value.source, receipt.payload_sha256, expected_active_publication_id=None)
    assert publisher.publish(value.source, receipt.payload_sha256, expected_active_publication_id=None) == result
    source = value.source
    assert db('SELECT count(*) AS n FROM server_embedding_fragments WHERE embedding_set_id=%s', (source.embedding_set_id,))[0]['n'] == 3
    assert db('SELECT count(*) AS n FROM server_embedding_vectors WHERE embedding_set_id=%s', (source.embedding_set_id,))[0]['n'] == 0
    context = str(uuid4())
    db('''INSERT INTO server_execution_contexts
        (context_id,owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id)
        VALUES (%s,%s,%s,%s,%s,%s)''',
        (context,source.owner_user_id,source.paper_id,source.version_id,result.publication_id,source.parse_revision_id))
    binding = PostgresRemoteContextResolver(pool).resolve(source.owner_user_id,context)
    hits = PostgresRemoteScopedSearch(pool, lambda *_:(1.,0.)).search('합성',binding,RemoteSearchFilters(),5)
    assert [h.chunk_id for h in hits] == list(value.embeddings.chunk_ids)


@pytest.mark.parametrize('fault', ['gap','set','parent','count','policy'])
def test_invalid_mapping_never_reaches_staging(pool, db, fault):
    store, value = setup(pool, db)
    value = fragmented(value)
    batch = value.embeddings
    fragments = list(batch.fragments)
    if fault == 'gap':
        batch = replace(batch, fragments=tuple(fragments[:-1]),vectors=batch.vectors[:-1])
    elif fault == 'policy': batch = replace(batch,split_policy={**batch.split_policy,'stride_tokens':89})
    else:
        change = {'set':dict(embedding_set_id=str(uuid4())), 'parent':dict(parent_chunk_id=str(uuid4())),
                  'count':dict(parent_token_count=249)}[fault]
        fragments[0] = replace(fragments[0],**change)
        batch = replace(batch,fragments=tuple(fragments))
    with pytest.raises(ProcessingStagingError): store.save(replace(value,embeddings=batch))
    assert db('SELECT count(*) AS n FROM server_processing_result_staging WHERE job_id=%s', (value.source.job_id,))[0]['n'] == 0
