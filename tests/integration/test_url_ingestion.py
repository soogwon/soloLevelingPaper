import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.fixtures.pdf_builder import build_minimal_pdf
from solo_leveling.workers import ingestion as ingestion_module
from solo_leveling.infrastructure.parsing import url_ingest as url_ingest_module
from solo_leveling.domain.models import JobStatus


def _fake_embed_texts(texts, model_name=None):
    import hashlib

    return [[b / 255.0 for b in hashlib.sha256(t.encode()).digest()[:16]] for t in texts]


def _fake_embedding_dimension(model_name=None):
    return 16


@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch):
    monkeypatch.setattr(ingestion_module, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(ingestion_module, "embedding_dimension", _fake_embedding_dimension)


def _fake_response(content_type="", content=b"", text=""):
    resp = MagicMock()
    resp.headers = {"Content-Type": content_type}
    resp.content = content
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def test_register_url_pdf_reuses_pdf_pipeline(tmp_path, monkeypatch):
    pdf_bytes = build_minimal_pdf(["Attention mechanisms enable parallel processing of sequences."])
    resp = _fake_response(content_type="application/pdf", content=pdf_bytes)
    monkeypatch.setattr(url_ingest_module.requests, "get", lambda *a, **k: resp)

    db_path = str(tmp_path / "db.sqlite")
    chroma_dir = str(tmp_path / "chroma")
    tmp_pdf_dir = str(tmp_path / "tmp_pdfs")

    result = ingestion_module.register_and_ingest_url(
        db_path, chroma_dir, "https://example.com/paper.pdf", tmp_pdf_dir
    )

    assert result["status"] == "ready"
    assert result["source_kind"] == "url_pdf"
    assert result["chunk_count"] == 1


def test_register_url_html_creates_single_page_chunks(tmp_path, monkeypatch):
    html = "<html><body><h1>Paper Title</h1><p>" + ("Some finding about attention. " * 40) + "</p></body></html>"
    resp = _fake_response(content_type="text/html", content=html.encode(), text=html)
    monkeypatch.setattr(url_ingest_module.requests, "get", lambda *a, **k: resp)

    db_path = str(tmp_path / "db.sqlite")
    chroma_dir = str(tmp_path / "chroma")
    tmp_pdf_dir = str(tmp_path / "tmp_pdfs")

    result = ingestion_module.register_and_ingest_url(
        db_path, chroma_dir, "https://example.com/blog-post", tmp_pdf_dir
    )

    assert result["status"] == "ready"
    assert result["source_kind"] == "url_html"
    assert result["chunk_count"] >= 1

    from solo_leveling.infrastructure.database import repository as repo

    chunks = repo.get_chunks_by_parse_revision(db_path, result["parse_revision_id"])
    assert all(c.pdf_page == 1 for c in chunks)  # HTML은 항상 페이지 1
    assert all(c.printed_page_label is None for c in chunks)


def test_register_url_rejects_private_network(tmp_path):
    db_path = str(tmp_path / "db.sqlite")
    chroma_dir = str(tmp_path / "chroma")
    tmp_pdf_dir = str(tmp_path / "tmp_pdfs")

    with pytest.raises(Exception):  # UnsafeUrlError
        ingestion_module.register_and_ingest_url(
            db_path, chroma_dir, "http://127.0.0.1/paper.pdf", tmp_pdf_dir
        )


def test_register_url_html_idempotent_on_same_content(tmp_path, monkeypatch):
    html = "<html><body><p>Same content every time.</p></body></html>"
    resp = _fake_response(content_type="text/html", content=html.encode(), text=html)
    monkeypatch.setattr(url_ingest_module.requests, "get", lambda *a, **k: resp)

    db_path = str(tmp_path / "db.sqlite")
    chroma_dir = str(tmp_path / "chroma")
    tmp_pdf_dir = str(tmp_path / "tmp_pdfs")

    first = ingestion_module.register_and_ingest_url(
        db_path, chroma_dir, "https://example.com/page", tmp_pdf_dir, paper_id="fixed-id"
    )
    second = ingestion_module.register_and_ingest_url(
        db_path, chroma_dir, "https://example.com/page", tmp_pdf_dir, paper_id="fixed-id"
    )

    assert first["reused_existing"] is False
    assert second["reused_existing"] is True
