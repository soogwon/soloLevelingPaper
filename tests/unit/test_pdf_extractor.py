import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.fixtures.pdf_builder import write_minimal_pdf
from solo_leveling.infrastructure.parsing.pdf_extractor import extract_pages, page_count


def test_extract_pages_preserves_page_boundaries(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    write_minimal_pdf(str(pdf_path), ["First page text.", "Second page text.", "Third page text."])

    pages = extract_pages(str(pdf_path))

    assert len(pages) == 3
    assert pages[0].pdf_page == 1
    assert pages[1].pdf_page == 2
    assert pages[2].pdf_page == 3
    assert "First page" in pages[0].text
    assert "Second page" in pages[1].text
    assert "Third page" in pages[2].text


def test_extract_pages_does_not_merge_text_across_pages(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    write_minimal_pdf(str(pdf_path), ["Alpha content.", "Beta content."])

    pages = extract_pages(str(pdf_path))

    assert "Beta" not in pages[0].text
    assert "Alpha" not in pages[1].text


def test_printed_page_label_is_none_by_default(tmp_path):
    """22번 문서: 자동 인식 방식 미정 — 추측해서 채우지 않음"""
    pdf_path = tmp_path / "sample.pdf"
    write_minimal_pdf(str(pdf_path), ["Some text."])

    pages = extract_pages(str(pdf_path))

    assert pages[0].printed_page_label is None


def test_page_count_matches_extract_pages(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    write_minimal_pdf(str(pdf_path), ["A", "B", "C", "D"])

    assert page_count(str(pdf_path)) == 4
    assert len(extract_pages(str(pdf_path))) == 4
