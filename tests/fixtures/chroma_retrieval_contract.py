"""실제 SQLite/Chroma 준비. 임시 경로와 명시적 벡터만 사용한다."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from contextlib import closing

from tests.fixtures.shared_retrieval_contract import rows, QUERY_VECTOR, MODEL, QUESTION
from solo_leveling.application.evidence_qa.search import ContextSearchService
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.models import (
    Chunk, Paper, PaperVersion, ParseRevision, ProcessingJob, EmbeddingSet, LearningContext, PaperSection,
)
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import init_db, get_connection
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteChromaRetriever, SQLiteContextReader
from solo_leveling.infrastructure.storage.vector_store import upsert_chunk_embeddings, verify_chunk_embeddings
from solo_leveling.infrastructure.translation.fake_provider import FakeTranslationProvider


class ChromaContractStore:
    metric = 'squared_l2'

    def __init__(self, db_path, client):
        self.db, self.client = str(db_path), client
        init_db(self.db)
        repo.insert_paper(self.db, Paper('paper'))
        repo.insert_version(self.db, PaperVersion('v1', 'paper', 'hash', '/synthetic/unused.pdf', 'synthetic.pdf'))
        self.embed = Mock(side_effect=lambda *_a, **_kw: [list(QUERY_VECTOR)])
        self.retriever = SQLiteChromaRetriever(self.db, client, embedder=self.embed, hybrid=False)
        self.service = ContextSearchService(SQLiteContextReader(self.db), self.retriever)

    def publish(self, offset=0, *, partial=False, base=None):
        values = rows(offset, partial=partial)
        parse, translation, embedding, context, job = [f'{name}-{offset}' for name in ('parse', 'tr', 'set', 'ctx', 'job')]
        chunks = [Chunk(r.chunk_id, parse, i, r.original, pdf_page=r.page, section_id=r.section)
                  for i, r in enumerate(values)]
        revision = ParseRevision(parse, 'v1', 9)
        sections = [PaperSection('s4', parse, 'Section four', 1, 1, 6, 7),
                    PaperSection('s5', parse, 'Section five', 2, 1, 6, 6),
                    PaperSection('s6', parse, 'Section six', 3, 1, 8, 8)]
        if base is None:
            repo.insert_parse_revision(self.db, revision)
            repo.insert_chunks(self.db, chunks)
            repo.insert_sections(self.db, sections)
        else:
            repo.prepare_reparse(self.db, ProcessingJob(job, 'v1'), f'request-{offset}', 'a'*64, base.parse, base.embedding)
            repo.save_reparse_result(self.db, job, revision, chunks, sections)
        provider = FakeTranslationProvider({r.original: r.korean for r in values},
                                          failures=tuple(r.chunk_id for r in values if not r.translated))
        batch = TranslationService(provider, lambda: translation).translate(chunks, TranslationSettings('fake', 'fixture', 'v1'))
        repo.save_translation_batch(self.db, batch)
        indexed = [r for r in values if r.translated]
        ids, docs = [r.chunk_id for r in indexed], [r.korean for r in indexed]
        upsert_chunk_embeddings(self.client, ids, [list(r.vector) for r in indexed], embedding, 'paper', 'v1', docs)
        verify_chunk_embeddings(self.client, ids, docs, embedding, 'paper', 'v1', 2)
        model = EmbeddingSet(embedding, translation, MODEL, 2)
        if base is None:
            repo.claim_ingestion_job(self.db, ProcessingJob(job, 'v1'))
            repo.publish_search_index(self.db, 'v1', parse, model, job, ids, [])
        else:
            repo.publish_reparsed_index(self.db, job, model, ids, [])
        repo.create_learning_context(self.db, LearningContext(context, 'v1', parse, translation, embedding_set_id=embedding))
        return NS(rows=values, version='v1', parse=parse, translation=translation, embedding=embedding, context=context)

    def search(self, snapshot, case):
        return self.service.search(QUESTION, snapshot.context, case.top_k,
                                   pdf_pages=case.pages, section_ids=case.sections)

    def active_embedding(self, snapshot):
        return repo.get_search_index(self.db, snapshot.version)['embedding_set_id']

    def native_distances(self, snapshot):
        result = self.client.get_collection(f'chunks-{snapshot.embedding}').query(
            query_embeddings=[list(QUERY_VECTOR)], n_results=sum(r.translated for r in snapshot.rows),
            include=['distances'])
        return tuple(zip(result['ids'][0], result['distances'][0], strict=True))

    def assert_partial(self, snapshot):
        with closing(get_connection(self.db)) as conn:
            assert conn.execute('SELECT count(*) FROM chunks WHERE parse_revision_id=?', (snapshot.parse,)).fetchone()[0] == 8
            results = conn.execute('SELECT chunk_id,failure_code FROM translation_results WHERE translation_revision_id=?',
                                   (snapshot.translation,)).fetchall()
        assert len(results) == 8 and sum(r['failure_code'] is None for r in results) == 7
        omitted = next(r.chunk_id for r in snapshot.rows if not r.translated)
        assert any(r['chunk_id'] == omitted and r['failure_code'] is not None for r in results)
        ids = self.client.get_collection(f'chunks-{snapshot.embedding}').get()['ids']
        assert len(ids) == 7 and omitted not in ids
