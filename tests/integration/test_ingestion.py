import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.fixtures.pdf_builder import write_minimal_pdf
from solo_leveling.workers import ingestion as ingestion_module
from solo_leveling.workers.ingestion import register_and_ingest, compute_file_hash
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.storage.vector_store import get_client, query_similar
from solo_leveling.domain.models import JobStatus


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

    # embedding_set_id를 직접 조회할 방법이 없으므로, Chroma 메타데이터를 통해 확인
    collection = client.get_or_create_collection("chunks")
    all_items = collection.get()
    embedding_set_id = all_items["metadatas"][0]["embedding_set_id"]

    hits = query_similar(client, query_vec, embedding_set_id=embedding_set_id, top_k=3)
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


def test_compute_file_hash_is_stable(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    write_minimal_pdf(str(pdf_path), ["content"])

    h1 = compute_file_hash(str(pdf_path))
    h2 = compute_file_hash(str(pdf_path))
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex
