"""실제 PostgreSQL 게시 포트와 pgvector 검색/source 포트를 사용하는 합성 준비 도우미."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

from tests.fixtures.shared_retrieval_contract import rows, QUERY_VECTOR, QUESTION, FINGERPRINT
from tests.integration.test_remote_processing_postgres import (
    make_paper, claimed_job, insert_revisions, insert_chunk, insert_translation, mark_ready, publish, new_id,
)
from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchFilters, validate_remote_hits
from solo_leveling.application.evidence_qa.remote_search import QueryEmbedding, FingerprintCheckedEmbedder
from solo_leveling.application.evidence_qa.remote_sources import assemble_remote_sources
from solo_leveling.interfaces.http.remote_sources import PostgresRemoteSourceReader
from solo_leveling.interfaces.http.remote_retrieval import PostgresRemoteContextResolver
from solo_leveling.interfaces.http.remote_vectors import EmbeddingWriteScope, PostgresEmbeddingVectorWriter, PostgresRemoteScopedSearch


class PgvectorContractStore:
    metric = 'cosine'

    def __init__(self, db, stores, pool):
        self.db, self.stores, self.pool = db, stores, pool
        self.owner = f'contract-{new_id()}'
        self.embed = Mock(side_effect=lambda *_: QueryEmbedding(QUERY_VECTOR, json.dumps(FINGERPRINT)))
        self.retriever = PostgresRemoteScopedSearch(pool, FingerprintCheckedEmbedder(NS(embed=self.embed)))
        self.resolver = PostgresRemoteContextResolver(pool)
        self.reader = PostgresRemoteSourceReader(pool)

    def publish(self, offset=0, *, partial=False, base=None, vectors=None):
        values = rows(offset, partial=partial)
        paper = base.paper if base else make_paper(self.db, self.owner)
        job = claimed_job(self.stores[0], self.owner, paper)
        indexed = [r for r in values if r.translated]
        parse, translation, embedding = insert_revisions(self.db, self.owner, paper, job,
                                                         chunks=len(values), translated=len(indexed))
        self.db('UPDATE server_embedding_sets SET model_fingerprint=%s::jsonb WHERE embedding_set_id=%s',
                (json.dumps(FINGERPRINT), embedding))
        scope = EmbeddingWriteScope(self.owner, paper, 'v1', parse, translation, embedding, 2)
        writer = PostgresEmbeddingVectorWriter(self.pool)
        for ordinal, row in enumerate(values):
            insert_chunk(self.db, self.owner, paper, parse, ordinal, row.chunk_id)
            self.db('''UPDATE server_processing_chunks SET pdf_page=%s,section_id=%s,original_text=%s
                       WHERE owner_user_id=%s AND chunk_id=%s''',
                    (row.page, row.section, row.original, self.owner, row.chunk_id))
            if row.translated:
                insert_translation(self.db, self.owner, paper, parse, translation, row.chunk_id)
                self.db('''UPDATE server_translation_chunks SET translated_text=%s
                           WHERE owner_user_id=%s AND translation_revision_id=%s AND chunk_id=%s''',
                        (row.korean, self.owner, translation, row.chunk_id))
                writer.put(scope, row.chunk_id, row.vector if vectors is None else vectors)
        if partial:
            self.db('UPDATE server_translation_revisions SET omissions=%s::jsonb WHERE translation_revision_id=%s',
                    (json.dumps([{'chunk_id': r.chunk_id, 'reason': 'provider_unavailable'}
                                 for r in values if not r.translated]), translation))
        mark_ready(self.db, parse, translation, embedding)
        publication = publish(self.stores[1], self.owner, paper,
            (parse, translation, embedding, [r.chunk_id for r in values]), job, policy='partial' if partial else 'complete')
        context = new_id()
        self.db('''INSERT INTO server_execution_contexts
            (context_id,owner_user_id,paper_id,server_version_id,publication_id,parse_revision_id)
            VALUES (%s,%s,%s,'v1',%s,%s)''', (context, self.owner, paper, publication.publication_id, parse))
        return NS(rows=values, version='v1', parse=parse, translation=translation, embedding=embedding,
                  context=context, paper=paper, publication=publication.publication_id)

    def search(self, snapshot, case):
        binding = self.resolver.resolve(self.owner, snapshot.context)
        assert binding is not None and binding.publication_id == snapshot.publication
        filters = RemoteSearchFilters(case.pages, case.sections, case.strict_pages)
        hits = self.retriever.search(QUESTION, binding, filters, case.top_k)
        return assemble_remote_sources(QUESTION, binding, filters,
            validate_remote_hits(binding, filters, hits, case.top_k), self.reader).search

    def active_embedding(self, snapshot):
        return self.stores[1].get_active(self.owner, snapshot.paper, snapshot.version).embedding_set_id

    def native_distances(self, snapshot):
        binding = self.resolver.resolve(self.owner, snapshot.context)
        assert binding is not None
        hits = self.retriever.search(QUESTION, binding, RemoteSearchFilters(), 20)
        return tuple((h.chunk_id, h.cosine_distance) for h in hits)

    def assert_partial(self, snapshot):
        assert self.db('SELECT count(*) AS n FROM server_processing_chunks WHERE parse_revision_id=%s',
                       (snapshot.parse,))[0]['n'] == 8
        for table in ('server_translation_chunks', 'server_embedding_vectors'):
            assert self.db(f'SELECT count(*) AS n FROM {table} WHERE translation_revision_id=%s',
                           (snapshot.translation,))[0]['n'] == 7
        publication = self.stores[1].get_active(self.owner, snapshot.paper, snapshot.version)
        assert publication.policy == 'partial'
        assert [r['chunk_id'] for r in publication.manifest['omissions']] == [
            r.chunk_id for r in snapshot.rows if not r.translated]
