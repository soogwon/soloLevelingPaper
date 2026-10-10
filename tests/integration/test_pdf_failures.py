"""읽을 수 없는 PDF와 글자가 없는 페이지가 작업 기록(limitations)에 사유로 남는지 확인한다."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.fixtures.pdf_builder import build_minimal_pdf, write_minimal_pdf
from solo_leveling.application.translation.service import TranslationService
from solo_leveling.domain.translation import TranslationSettings
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.parsing.pdf_extractor import PdfExtractionError
from solo_leveling.workers import ingestion as ingestion_module
from solo_leveling.workers.ingestion import register_and_ingest


class _Provider:
    def translate(self, request, settings):
        return f"번역문 {request.chunk_id}"


def _fake_embed_texts(texts, model_name=None):
    import hashlib
    return [[b / 255.0 for b in hashlib.sha256(t.encode()).digest()[:16]] for t in texts]


@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch):
    monkeypatch.setattr(ingestion_module, "embed_texts", _fake_embed_texts)
    monkeypatch.setattr(ingestion_module, "embedding_dimension", lambda model_name=None: 16)


def _register(db_path, chroma_dir, pdf_path):
    return register_and_ingest(
        db_path, chroma_dir, pdf_path,
        translation_service=TranslationService(_Provider()),
        translation_settings=TranslationSettings("fake", "fixture", "prompt1"),
    )


def _job_limitations(db_path):
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT status, limitations FROM processing_jobs").fetchall()
    assert len(rows) == 1
    return rows[0][0], json.loads(rows[0][1])


def test_unreadable_pdf_records_reason_code(tmp_path):
    data = build_minimal_pdf(["Attention is all you need."])
    pdf_path = tmp_path / "truncated.pdf"
    pdf_path.write_bytes(data[: int(len(data) * 0.55)])
    db_path, chroma_dir = str(tmp_path / "db.sqlite"), str(tmp_path / "chroma")

    with pytest.raises(PdfExtractionError):
        _register(db_path, chroma_dir, str(pdf_path))

    assert _job_limitations(db_path) == ("failed", ["pdf_unreadable"])


def test_page_without_text_is_listed_in_limitations(tmp_path):
    pdf_path = tmp_path / "mixed.pdf"
    write_minimal_pdf(str(pdf_path), [
        "First page has searchable text about attention mechanisms.",
        "",
        "Third page also has searchable text about positional encoding.",
    ])
    db_path, chroma_dir = str(tmp_path / "db.sqlite"), str(tmp_path / "chroma")

    result = _register(db_path, chroma_dir, str(pdf_path))

    assert result["status"] == "ready"
    assert result["limitations"] == ["empty_pages:2"]
    chunks = repo.get_chunks_by_parse_revision(db_path, result["parse_revision_id"])
    assert {c.pdf_page for c in chunks} == {1, 3}
    assert _job_limitations(db_path) == ("ready", ["empty_pages:2"])


def test_all_pages_with_text_have_no_empty_page_limitation(tmp_path):
    pdf_path = tmp_path / "full.pdf"
    write_minimal_pdf(str(pdf_path), ["Page one has text.", "Page two has text too."])
    db_path, chroma_dir = str(tmp_path / "db.sqlite"), str(tmp_path / "chroma")

    result = _register(db_path, chroma_dir, str(pdf_path))

    assert result["limitations"] == []
