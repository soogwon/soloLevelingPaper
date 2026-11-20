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
from solo_leveling.domain.translation import TranslationStatus,TranslationFailureCode
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


def test_original_only_keeps_failure_reason_and_is_searchable(pool,db):
    from solo_leveling.interfaces.http.remote_sources import PostgresRemoteSourceReader
    store,value = setup(pool,db)
    value = fragmented(value)
    original = value.document.chunks[0].original_text
    value = replace(value,translations=(replace(value.translations[0],text=original,
        status=TranslationStatus.ORIGINAL_ONLY,failure_code=TranslationFailureCode.UNCHANGED_TRANSLATION),))
    receipt = store.save(value)
    assert store.restore(store.load(value.source)) == value
    published = PostgresStagedProcessingPublisher(store).publish(value.source,receipt.payload_sha256,
                                                                expected_active_publication_id=None)
    source = value.source
    row = db('SELECT translation_status,translation_failure_code FROM server_translation_chunks WHERE translation_revision_id=%s',
             (source.translation_revision_id,))[0]
    assert row == dict(translation_status='original_only',translation_failure_code='unchanged_translation')
    context = str(uuid4())
    db('''INSERT INTO server_execution_contexts
        (context_id,owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id)
        VALUES (%s,%s,%s,%s,%s,%s)''',(context,source.owner_user_id,source.paper_id,source.version_id,
                                     published.publication_id,source.parse_revision_id))
    binding = PostgresRemoteContextResolver(pool).resolve(source.owner_user_id,context)
    hits = PostgresRemoteScopedSearch(pool,lambda *_:(1.,0.)).search('합성',binding,RemoteSearchFilters(),5)
    rows = PostgresRemoteSourceReader(pool).read_chunks(binding,tuple(h.chunk_id for h in hits))
    assert len(rows) == 1 and rows[0].to_chunk().text is None
    assert rows[0].to_chunk().original_text == original


@pytest.mark.parametrize('use_fragments', [False, True])
def test_partial_publication_preserves_failure_and_allows_mcp_learning(pool, db, restricted, use_fragments):
    from solo_leveling.domain.remote_processing import ProcessedTranslation
    from solo_leveling.interfaces.http.remote_learning import PostgresRemoteLearningStore
    from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries

    _, value = setup(pool, db)
    store = make_store(restricted[0])
    if use_fragments:
        value = fragmented(value)
    source = value.source
    missing = replace(value.document.chunks[0], chunk_id=str(uuid4()), chunk_index=1,
                      original_text='Untranslated text.', section_id=None)
    value = replace(value, document=replace(value.document,chunks=(*value.document.chunks,missing)),
        translations=(*value.translations, ProcessedTranslation(missing.chunk_id, None,
                     TranslationFailureCode.TARGET_LANGUAGE_MISMATCH, TranslationStatus.TRANSLATION_FAILED)))
    receipt = store.save(value)
    assert store.restore(store.load(source)) == value
    publisher = PostgresStagedProcessingPublisher(store)
    published = publisher.publish(source,receipt.payload_sha256,expected_active_publication_id=None)
    assert publisher.publish(source,receipt.payload_sha256,expected_active_publication_id=None) == published
    assert published.policy == 'partial'
    assert published.manifest == dict(chunk_count=2,translated_chunk_count=1,indexed_chunk_count=1,
        omissions=[dict(chunk_id=missing.chunk_id,reason='target_language_mismatch')],policy='partial')
    assert db('SELECT 1 FROM server_processing_chunks WHERE parse_revision_id=%s AND chunk_id=%s',
              (source.parse_revision_id,missing.chunk_id))
    assert not db('SELECT 1 FROM server_translation_chunks WHERE chunk_id=%s',(missing.chunk_id,))
    table = 'server_embedding_fragments' if use_fragments else 'server_embedding_vectors'
    assert db(f'SELECT DISTINCT chunk_id::text FROM {table} WHERE embedding_set_id=%s',
              (source.embedding_set_id,)) == [{'chunk_id':value.document.chunks[0].chunk_id}]
    assert db('SELECT document_metadata FROM server_parse_revisions WHERE parse_revision_id=%s',
              (source.parse_revision_id,))[0]['document_metadata']['translation_status_counts'] == {
                  'translated': 1, 'translation_failed': 1,
              }
    queries = RemoteProductQueries(pool)
    queries.owner = lambda: source.owner_user_id
    context = str(uuid4())
    opened = queries.open_paper(source.paper_id,source.version_id,context)
    assert opened['status'] == 'ok' and opened['publication_policy'] == 'partial'
    learning = PostgresRemoteLearningStore(pool)
    assert learning.start(source.owner_user_id,context)['status'] == 'reading'
    assert learning.get('other-owner',context) is None
