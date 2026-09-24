import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.fixtures.pdf_builder import write_minimal_pdf
from solo_leveling.workers import ingestion as ingestion_module
from solo_leveling.workers.ingestion import register_and_ingest as _register_and_ingest, compute_file_hash
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.application.translation.ports import TranslationProviderUnavailable
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.storage.vector_store import get_client, query_similar
from solo_leveling.domain.models import JobStatus, LearningContext, Evidence


class FixtureProvider:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def translate(self, request, settings):
        self.calls.append(request)
        if self.fail:
            raise TranslationProviderUnavailable('simulated private provider details')
        return f'시험용 한국어 번역문 {request.chunk_id}'


def register_and_ingest(*args, **kwargs):
    kwargs.setdefault('translation_service', TranslationService(FixtureProvider()))
    kwargs.setdefault('translation_settings', TranslationSettings('fake', 'fixture', 'prompt1'))
    return _register_and_ingest(*args, **kwargs)


def _fake_embed_texts(texts, model_name=None):
    """
    이 샌드박스는 huggingface.co에 접근할 수 없어 실제 모델을 받을 수 없다.
    테스트에서는 결정론적 가짜 임베딩(텍스트 해시 기반)으로 교체한다 —
    실제 배포 환경(인터넷 되는 팀원 PC)에서는 이 mock 없이 진짜
    sentence-transformers가 그대로 쓰인다.
    """
    import hashlib

    vectors = []
    for t in texts:
        h = hashlib.sha256(t.encode()).digest()
        vec = [b / 255.0 for b in h[:16]]  # 16차원 가짜 벡터
        vectors.append(vec)
    return vectors


def _fake_embedding_dimension(model_name=None):
    return 16


@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch):
    monkeypatch.setattr(ingestion_module, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(ingestion_module, "embedding_dimension", _fake_embedding_dimension)


def test_end_to_end_ingestion_produces_page_bound_chunks_and_vectors(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    write_minimal_pdf(
        str(pdf_path),
        [
            "Transformer architecture relies on self attention mechanisms for parallel processing.",
            "Positional encoding is added because attention has no notion of sequence order.",
        ],
    )
    db_path = str(tmp_path / "db.sqlite")
    chroma_dir = str(tmp_path / "chroma")

    result = register_and_ingest(db_path, chroma_dir, str(pdf_path), original_filename="paper.pdf")

    assert result["status"] == "ready"
    assert result["chunk_count"] == 2  # 짧은 페이지 2개 → 각 1청크
    assert result["reused_existing"] is False

    job = repo.get_job(db_path, result["job_id"])
    assert job.status == JobStatus.READY

    chunks = repo.get_chunks_by_parse_revision(db_path, result["parse_revision_id"])
    assert len(chunks) == 2
    assert all(c.text.startswith('시험용 한국어') for c in chunks)
    assert all(c.translation_revision_id == result['translation_revision_id'] for c in chunks)
    assert repo.get_search_index(db_path, result['version_id'])['embedding_set_id'] == result['embedding_set_id']
    assert {c.pdf_page for c in chunks} == {1, 2}
    # 페이지 경계를 넘지 않았는지 확인
    page1 = [c for c in chunks if c.pdf_page == 1][0]
    page2 = [c for c in chunks if c.pdf_page == 2][0]
    assert "Positional" not in page1.original_text
    assert "attention mechanisms" not in page2.original_text


def test_end_to_end_vectors_are_queryable(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    write_minimal_pdf(
        str(pdf_path),
        ["Self attention allows parallel computation unlike recurrent networks."],
    )
    db_path = str(tmp_path / "db.sqlite")
    chroma_dir = str(tmp_path / "chroma")

    result = register_and_ingest(db_path, chroma_dir, str(pdf_path))

    client = get_client(chroma_dir)
    query_vec = _fake_embed_texts(["parallel computation attention"])[0]

    collection_name = f"chunks-{result['embedding_set_id']}"
    collection = client.get_or_create_collection(collection_name)
    all_items = collection.get()
    embedding_set_id = all_items["metadatas"][0]["embedding_set_id"]

    assert all(doc.startswith('시험용 한국어') for doc in all_items['documents'])
    hits = query_similar(client, query_vec, embedding_set_id=embedding_set_id, top_k=3,
                         collection_name=collection_name)
    assert len(hits) >= 1
    assert hits[0]["similarity"] > 0


def test_idempotent_reingestion_of_same_file(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    write_minimal_pdf(str(pdf_path), ["Same content every time."])
    db_path = str(tmp_path / "db.sqlite")
    chroma_dir = str(tmp_path / "chroma")

    first = register_and_ingest(db_path, chroma_dir, str(pdf_path), paper_id="fixed-paper-id")
    second = register_and_ingest(db_path, chroma_dir, str(pdf_path), paper_id="fixed-paper-id")

    assert first["reused_existing"] is False
    assert second["reused_existing"] is True
    assert second["version_id"] == first["version_id"]
    assert second['parse_revision_id'] == first['parse_revision_id']
    assert second['embedding_set_id'] == first['embedding_set_id']


def test_compute_file_hash_is_stable(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    write_minimal_pdf(str(pdf_path), ["content"])

    h1 = compute_file_hash(str(pdf_path))
    h2 = compute_file_hash(str(pdf_path))
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 해시의 16진수 표현 길이


def test_all_translation_failure_then_retry_same_version(tmp_path):
    pdf = tmp_path / 'retry.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    db, vectors = str(tmp_path / 'data.sqlite'), str(tmp_path / 'chroma')
    provider = FixtureProvider(fail=True)
    service = TranslationService(provider)
    first = register_and_ingest(db, vectors, str(pdf), paper_id='p', translation_service=service)
    assert first['status'] == 'failed'
    assert repo.get_search_index(db, first['version_id']) is None
    assert repo.get_job(db, first['job_id']).status == JobStatus.FAILED
    assert 'private' not in str(first)
    provider.fail = False
    second = register_and_ingest(db, vectors, str(pdf), paper_id='p', translation_service=service)
    assert second['status'] == 'ready'
    assert second['version_id'] == first['version_id']
    assert second['parse_revision_id'] == first['parse_revision_id']
    assert second['translation_revision_id'] == first['translation_revision_id']
    assert second['limitations'] == []


def test_partial_translation_indexes_only_successes(tmp_path):
    class PartialProvider(FixtureProvider):
        def translate(self, request, settings):
            if request.original_text == 'Second page.':
                raise TranslationProviderUnavailable()
            return super().translate(request, settings)

    pdf = tmp_path / 'partial.pdf'
    write_minimal_pdf(str(pdf), ['First page.', 'Second page.'])
    db, vectors = str(tmp_path / 'data.sqlite'), str(tmp_path / 'chroma')
    result = register_and_ingest(db, vectors, str(pdf), translation_service=TranslationService(PartialProvider()))
    assert result['status'] == 'ready'
    assert result['chunk_count'] == 1
    assert len(result['limitations']) == 1
    chunks = repo.get_chunks_by_parse_revision(db, result['parse_revision_id'])
    assert chunks[1].text is None
    client = get_client(vectors)
    collection = client.get_or_create_collection(f"chunks-{result['embedding_set_id']}")
    assert collection.get()['ids'] == [chunks[0].chunk_id]


def test_index_failure_preserves_translation_and_retry_skips_provider(tmp_path, monkeypatch):
    pdf = tmp_path / 'index-failure.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    db, vectors = str(tmp_path / 'data.sqlite'), str(tmp_path / 'chroma')
    provider = FixtureProvider()
    service = TranslationService(provider)
    original_verify = ingestion_module.verify_chunk_embeddings

    def fail_verify(*args, **kwargs):
        raise ValueError('private index details')

    monkeypatch.setattr(ingestion_module, 'verify_chunk_embeddings', fail_verify)
    with pytest.raises(ValueError):
        register_and_ingest(db, vectors, str(pdf), paper_id='p', translation_service=service)
    version = repo.find_version_by_hash(db, 'p', compute_file_hash(str(pdf)))
    assert repo.get_search_index(db, version.version_id) is None
    parse = repo.get_latest_parse_revision(db, version.version_id)
    assert repo.get_chunks_by_parse_revision(db, parse.parse_revision_id)[0].text is not None
    client = get_client(vectors)
    assert all(client.get_collection(c.name).count() == 0 for c in client.list_collections())
    monkeypatch.setattr(ingestion_module, 'verify_chunk_embeddings', original_verify)
    result = register_and_ingest(db, vectors, str(pdf), paper_id='p', translation_service=service)
    assert result['status'] == 'ready'
    assert len(provider.calls) == 1


def test_published_registration_does_not_retranslate_after_config_change(tmp_path):
    pdf = tmp_path / 'unchanged.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    db, vectors = str(tmp_path / 'data.sqlite'), str(tmp_path / 'chroma')
    first = register_and_ingest(db, vectors, str(pdf), paper_id='p')
    provider = FixtureProvider(fail=True)
    second = register_and_ingest(db, vectors, str(pdf), paper_id='p',
        translation_service=TranslationService(provider),
        translation_settings=TranslationSettings('new-provider', 'new-model', 'new-prompt'))
    assert first['translation_revision_id'] == second['translation_revision_id']
    assert provider.calls == []


def test_empty_extraction_never_ready(tmp_path, monkeypatch):
    pdf = tmp_path / 'empty.pdf'
    write_minimal_pdf(str(pdf), [''])
    provider = FixtureProvider()
    result = register_and_ingest(str(tmp_path / 'db.sqlite'), str(tmp_path / 'chroma'), str(pdf),
                                 translation_service=TranslationService(provider))
    assert result['status'] == 'failed'
    assert result['limitations'] == ['extraction_empty']
    assert provider.calls == []


def test_registered_paper_context_and_evidence_roundtrip(tmp_path):
    pdf = tmp_path / 'context.pdf'
    write_minimal_pdf(str(pdf), ['First page.'])
    db = str(tmp_path / 'db.sqlite')
    result = register_and_ingest(db, str(tmp_path / 'chroma'), str(pdf))
    context = LearningContext('ctx', result['version_id'], result['parse_revision_id'],
                              result['translation_revision_id'], embedding_set_id=result['embedding_set_id'])
    repo.create_learning_context(db, context)
    chunk = repo.get_translated_chunks(db, context.parse_revision_id, context.translation_revision_id)[0]
    repo.save_evidences(db, context.context_id, [Evidence('e1', chunk.chunk_id, chunk.text, chunk.original_text)])
    assert repo.get_learning_context(db, 'ctx') == context
    detail = repo.get_evidences(db, 'ctx', ['e1']).evidence[0]
    assert detail.quote_original == 'First page.'
    assert detail.quote_ko == chunk.text
    assert detail.file_display_name == 'context.pdf'


def test_retry_only_missing_translations_after_index_failure(tmp_path, monkeypatch):
    class RecoveringProvider(FixtureProvider):
        def __init__(self):
            super().__init__()
            self.recovered = False

        def translate(self, request, settings):
            self.calls.append(request)
            if request.original_text == 'Second page.' and not self.recovered:
                raise TranslationProviderUnavailable()
            return '시험용 번역 ' + request.chunk_id

    pdf = tmp_path / 'resume.pdf'
    write_minimal_pdf(str(pdf), ['First page.', 'Second page.'])
    db, vectors = str(tmp_path / 'db.sqlite'), str(tmp_path / 'chroma')
    provider = RecoveringProvider()
    original_verify = ingestion_module.verify_chunk_embeddings
    monkeypatch.setattr(ingestion_module, 'verify_chunk_embeddings', lambda *a, **kw: (_ for _ in ()).throw(ValueError('index failure')))
    with pytest.raises(ValueError):
        register_and_ingest(db, vectors, str(pdf), paper_id='p', translation_service=TranslationService(provider))
    provider.recovered = True
    monkeypatch.setattr(ingestion_module, 'verify_chunk_embeddings', original_verify)
    result = register_and_ingest(db, vectors, str(pdf), paper_id='p', translation_service=TranslationService(provider))
    assert result['status'] == 'ready'
    assert result['chunk_count'] == 2
    assert [r.original_text for r in provider.calls] == ['First page.', 'Second page.', 'Second page.']
    assert result['limitations'] == []


def test_vector_sets_isolate_dimensions_and_detect_corruption(tmp_path):
    from solo_leveling.infrastructure.storage.vector_store import (
        upsert_chunk_embeddings, verify_chunk_embeddings, delete_by_version,
    )
    client = get_client(str(tmp_path / 'vectors'))
    upsert_chunk_embeddings(client, ['c1'], [[1.0, 0.0]], 'index-one', 'paper', 'v1', ['첫째'])
    upsert_chunk_embeddings(client, ['c2'], [[1.0, 0.0, 0.0]], 'index-two', 'paper', 'v2', ['둘째'])
    verify_chunk_embeddings(client, ['c1'], ['첫째'], 'index-one', 'paper', 'v1', 2)
    assert query_similar(client, [1.0, 0.0], 'index-one')[0]['chunk_id'] == 'c1'
    assert query_similar(client, [1.0, 0.0], 'index-one', paper_id='other') == []
    with pytest.raises(ValueError):
        verify_chunk_embeddings(client, ['c1'], ['잘못된 번역'], 'index-one', 'paper', 'v1', 2)
    delete_by_version(client, 'v1')
    assert client.get_collection('chunks-index-one').count() == 0
    assert client.get_collection('chunks-index-two').count() == 1
