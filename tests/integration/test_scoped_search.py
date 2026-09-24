"""실제 SQLite·Chroma와 고정 질문 벡터로 범위 검색을 검증한다."""

from contextlib import closing

import pytest
from chromadb.errors import NotFoundError

from solo_leveling.application.evidence_qa.search import ContextSearchService
from solo_leveling.application.evidence_qa.serialization import serialize_search_result
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.models import Chunk, EmbeddingSet, LearningContext, Paper, PaperVersion, ParseRevision, ProcessingJob
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection, init_db
from solo_leveling.infrastructure.retrieval.sqlite_chroma import SQLiteChromaRetriever, SQLiteContextReader
from solo_leveling.infrastructure.storage.vector_store import get_client, upsert_chunk_embeddings, verify_chunk_embeddings
from solo_leveling.infrastructure.translation.fake_provider import FakeTranslationProvider


@pytest.fixture
def setup_search(tmp_path):
    db = str(tmp_path / 'db.sqlite')
    init_db(db)
    client = get_client(str(tmp_path / 'chroma'))
    repo.insert_paper(db, Paper('paper'))
    repo.insert_version(db, PaperVersion('v', 'paper', 'hash', '/private/a.pdf', 'a.pdf'))
    repo.insert_parse_revision(db, ParseRevision('p', 'v', 3))
    chunks = [Chunk('a', 'p', 0, 'Attention.', pdf_page=1, section_id='intro'),
              Chunk('b', 'p', 1, 'Parallel.', pdf_page=2, section_id='method'),
              Chunk('c', 'p', 2, 'Computation.', pdf_page=3, section_id='method')]
    repo.insert_chunks(db, chunks)
    translations = {'Attention.': '어텐션 설명', 'Parallel.': '병렬 처리', 'Computation.': '연산 방법'}
    batch = TranslationService(FakeTranslationProvider(translations), lambda: 't').translate(
        chunks, TranslationSettings('fake', 'fixture', 'prompt1'))
    repo.save_translation_batch(db, batch)
    docs = [translations[c.original_text] for c in chunks]
    upsert_chunk_embeddings(client, ['a', 'b', 'c'], [[1., 0.], [0., 1.], [0., 1.]], 'idx', 'paper', 'v', docs)
    verify_chunk_embeddings(client, ['a', 'b', 'c'], docs, 'idx', 'paper', 'v', 2)
    repo.claim_ingestion_job(db, ProcessingJob('job', 'v'))
    repo.publish_search_index(db, 'v', 'p', EmbeddingSet('idx', 't', 'saved-model', 2), 'job', 3, [])
    repo.create_learning_context(db, LearningContext('ctx', 'v', 'p', 't', embedding_set_id='idx'))
    calls = []

    def embedder(texts, *, model_name):
        calls.append((texts, model_name))
        return [[1., 0.]]

    retriever = SQLiteChromaRetriever(db, client, embedder=embedder)
    service = ContextSearchService(SQLiteContextReader(db), retriever)
    return db, client, retriever, service, calls


def test_registered_pdf_can_be_searched_through_context(tmp_path, monkeypatch):
    from tests.fixtures.pdf_builder import write_minimal_pdf
    from solo_leveling.workers import ingestion

    def embedder(texts, *, model_name):
        return [[1., 0.] for _ in texts]

    monkeypatch.setattr(ingestion, 'embed_texts', embedder)
    monkeypatch.setattr(ingestion, 'embedding_dimension', lambda model: 2)
    pdf = tmp_path / 'paper.pdf'
    write_minimal_pdf(str(pdf), ['Attention.'])
    db, chroma = str(tmp_path / 'db.sqlite'), str(tmp_path / 'chroma')
    registered = ingestion.register_and_ingest(db, chroma, str(pdf), paper_id='paper',
        embedding_model='fixture-model',
        translation_service=TranslationService(FakeTranslationProvider({'Attention.': '어텐션 설명'})),
        translation_settings=TranslationSettings('fake', 'fixture', 'prompt1'))
    repo.create_learning_context(db, LearningContext('ctx', registered['version_id'],
        registered['parse_revision_id'], registered['translation_revision_id'],
        embedding_set_id=registered['embedding_set_id']))
    service = ContextSearchService(SQLiteContextReader(db),
        SQLiteChromaRetriever(db, get_client(chroma), embedder=embedder))
    result = service.search('어텐션이란?', 'ctx')
    assert result.embedding_set_id == registered['embedding_set_id']
    assert result.items[0].chunk.text == '어텐션 설명'
    assert result.items[0].chunk.pdf_page == 1


def test_search_returns_db_text_scores_ranks_and_saved_model(setup_search):
    db, client, _, service, calls = setup_search
    # 다른 컬렉션의 더 가까운 벡터도 검색에 섞이지 않는다.
    upsert_chunk_embeddings(client, ['outside'], [[1., 0.]], 'other', 'other', 'other', ['외부 번역'])
    result = service.search('어텐션은 무엇인가?', 'ctx', top_k=10)
    assert [r.chunk.chunk_id for r in result.items] == ['a', 'b', 'c']
    assert [r.rank for r in result.items] == [1, 2, 3]
    assert result.items[0].score == pytest.approx(1.)
    assert result.items[1].score == pytest.approx(1 / 3)
    assert calls == [(['어텐션은 무엇인가?'], 'saved-model')]
    assert result.items[0].chunk.original_text == 'Attention.'
    assert result.items[0].chunk.text == '어텐션 설명'
    assert result.items[1].chunk.pdf_page == 2
    payload = serialize_search_result(result)
    assert payload['retrieval_method'] == 'vector'
    assert payload['embedding_set_id'] == 'idx'
    assert '/private' not in str(payload)
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM evidences').fetchone()[0] == 0


def test_filters_apply_before_top_k_and_empty_scope_is_normal(setup_search):
    _, _, _, service, calls = setup_search
    result = service.search('질문', 'ctx', 1, pdf_pages=(2, 3), section_ids=('method',))
    assert [i.chunk.chunk_id for i in result.items] == ['b']
    assert service.search('질문', 'ctx', 1, pdf_pages=(3,)).items[0].chunk.chunk_id == 'c'
    calls.clear()
    empty = service.search('질문', 'ctx', pdf_pages=(1,), section_ids=('method',))
    assert empty.items == ()
    assert empty.embedding_set_id == 'idx'
    assert calls == []


def test_search_entry_creates_and_reuses_default_context(setup_search):
    from solo_leveling.application.evidence_qa.entry import SearchEntryService

    db, _, retriever, _, _ = setup_search
    service = SearchEntryService(SQLiteContextReader(db), retriever)
    first = service.search('어텐션이란?', version_id='v')
    second = service.search('다시 설명해줘', version_id='v', pdf_pages=(2,))
    assert first.context_id == second.context_id
    assert first.context_id != 'ctx'
    assert second.result.items[0].chunk.pdf_page == 2
    explicit = service.search('질문', context_id='ctx')
    assert explicit.context_id == 'ctx'
    assert repo.get_learning_context(db, first.context_id).goal == 'understand'


@pytest.mark.parametrize('vector', [[[1.]], [[float('nan'), 0.]], [[float('inf'), 0.]], []])
def test_invalid_question_vector_is_rejected(setup_search, vector):
    _, _, retriever, service, _ = setup_search
    retriever.embedder = lambda *a, **kw: vector
    with pytest.raises(ValueError):
        service.search('질문', 'ctx')


@pytest.mark.parametrize('damage', ['missing', 'text', 'metadata', 'extra', 'collection', 'revision', 'count'])
def test_inconsistent_index_is_not_returned_as_empty_success(setup_search, damage):
    db, client, _, service, calls = setup_search
    collection = client.get_collection('chunks-idx')
    if damage == 'missing':
        collection.delete(ids=['a'])
    elif damage == 'text':
        collection.update(ids=['a'], embeddings=[[1., 0.]], documents=['변조된 번역'])
    elif damage == 'metadata':
        collection.update(ids=['a'], metadatas=[{'version_id': 'other'}])
    elif damage == 'extra':
        collection.add(ids=['extra'], embeddings=[[1., 0.]], documents=['다른 청크'])
    elif damage == 'collection':
        client.delete_collection('chunks-idx')
    else:
        with closing(get_connection(db)) as conn, conn:
            if damage == 'revision':
                conn.execute("INSERT INTO parse_revisions VALUES ('other', 'v', 3, 'now')")
                conn.execute("UPDATE translation_revisions SET parse_revision_id='other' WHERE translation_revision_id='t'")
            else:
                conn.execute("UPDATE search_indexes SET chunk_count=2 WHERE embedding_set_id='idx'")
    with pytest.raises(NotFoundError if damage == 'collection' else ValueError):
        service.search('질문', 'ctx')
    assert calls == []
    if damage == 'collection':
        assert not client.list_collections()
