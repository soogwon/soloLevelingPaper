"""PDF를 열 수 없을 때 라이브러리 예외 대신 고정 코드의 PdfExtractionError가 나오는지 확인한다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pdfplumber
import pytest
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException

from tests.fixtures.pdf_builder import build_minimal_pdf
from solo_leveling.infrastructure.parsing import pdf_extractor
from solo_leveling.infrastructure.parsing.pdf_extractor import (
    PDF_ENCRYPTED, PDF_UNREADABLE, PdfExtractionError, extract_pages, page_count,
)


def test_truncated_pdf_is_reported_as_unreadable(tmp_path):
    data = build_minimal_pdf(["Some text on the only page."])
    path = tmp_path / "truncated.pdf"
    path.write_bytes(data[: int(len(data) * 0.55)])

    with pytest.raises(PdfExtractionError) as caught:
        extract_pages(str(path))

    assert caught.value.code == PDF_UNREADABLE
    assert "pdfminer" not in str(caught.value).lower()
    assert str(path) not in str(caught.value)


def test_text_file_with_pdf_extension_is_reported_as_unreadable(tmp_path):
    path = tmp_path / "fake.pdf"
    path.write_text("이 파일은 PDF가 아닙니다.\n", encoding="utf-8")

    with pytest.raises(PdfExtractionError) as caught:
        page_count(str(path))

    assert caught.value.code == PDF_UNREADABLE


def test_password_error_is_reported_as_encrypted(tmp_path, monkeypatch):
    """암호 PDF 파일을 만들 도구가 없어서, pdfplumber가 던지는 예외 모양을 그대로 흉내 낸다."""
    def raise_password(*args, **kwargs):
        raise PdfminerException(PDFPasswordIncorrect())

    monkeypatch.setattr(pdfplumber, "open", raise_password)

    with pytest.raises(PdfExtractionError) as caught:
        extract_pages(str(tmp_path / "locked.pdf"))

    assert caught.value.code == PDF_ENCRYPTED


def test_missing_file_keeps_original_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_pages(str(tmp_path / "nope.pdf"))


def test_unrelated_errors_are_not_swallowed(tmp_path, monkeypatch):
    def raise_other(*args, **kwargs):
        raise RuntimeError("unrelated failure")

    monkeypatch.setattr(pdf_extractor.pdfplumber, "open", raise_other)

    with pytest.raises(RuntimeError):
        extract_pages(str(tmp_path / "x.pdf"))
