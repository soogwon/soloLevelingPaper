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


# ── 쪽 번호 분리(#15) ─────────────────────────────────────────
from solo_leveling.infrastructure.parsing.chunker import chunk_pages


def test_footer_page_number_is_split_from_body(tmp_path):
    """이슈 #15 재현: 본문 끝에 쪽 번호 '6'이 붙어 청크가 '...when the sequence\\n6'으로 끝났다."""
    pdf_path = tmp_path / "footer.pdf"
    write_minimal_pdf(str(pdf_path), ["Each word is processed when the sequence", "Next page body."],
                      footers=["6", "7"])

    pages = extract_pages(str(pdf_path))

    assert pages[0].text.endswith("when the sequence")
    assert pages[0].page_number_candidate == "6"
    assert pages[1].text == "Next page body."
    assert pages[1].page_number_candidate == "7"


def test_last_chunk_of_page_does_not_end_with_page_number(tmp_path):
    pdf_path = tmp_path / "footer.pdf"
    write_minimal_pdf(str(pdf_path), ["Attention helps when the sequence"], footers=["6"])

    chunks = chunk_pages("rev", extract_pages(str(pdf_path)))

    assert chunks[-1].original_text.endswith("sequence")
    assert not any(c.original_text.rstrip().endswith("6") for c in chunks)


def test_page_number_is_not_used_as_printed_page_label(tmp_path):
    """쪽 번호 후보는 인용용 라벨로 쓰지 않는다(자동 인식 여부는 #31에서 결정)."""
    pdf_path = tmp_path / "footer.pdf"
    write_minimal_pdf(str(pdf_path), ["Body text."], footers=["6"])

    page = extract_pages(str(pdf_path))[0]

    assert page.printed_page_label is None
    assert page.page_number_candidate == "6"


def test_body_numbers_in_formulas_and_tables_are_kept(tmp_path):
    body = "Table 1: values\n0.5 12 7\nscale = 8\nwe use 6"
    pdf_path = tmp_path / "numbers.pdf"
    write_minimal_pdf(str(pdf_path), [body], footers=["3"])

    page = extract_pages(str(pdf_path))[0]

    assert page.text == body  # 본문 끝의 '6'과 표 숫자는 그대로
    assert page.page_number_candidate == "3"


def test_digits_only_last_body_line_is_kept_when_not_in_footer_margin(tmp_path):
    """여백이 아니라 본문 안에 있는 숫자 줄(수식 번호·표 값)은 지우지 않는다."""
    pdf_path = tmp_path / "bodynum.pdf"
    write_minimal_pdf(str(pdf_path), ["Equation follows\n42"])

    page = extract_pages(str(pdf_path))[0]

    assert page.text.endswith("\n42")
    assert page.page_number_candidate is None


def test_footer_with_other_text_is_not_treated_as_page_number(tmp_path):
    pdf_path = tmp_path / "footer-text.pdf"
    write_minimal_pdf(str(pdf_path), ["Body."], footers=["Page 6 of 15"])

    page = extract_pages(str(pdf_path))[0]

    assert page.text.endswith("Page 6 of 15") or "Page 6 of 15" in page.text
    assert page.page_number_candidate is None


def test_non_numeric_or_long_footer_numbers_are_kept(tmp_path):
    pdf_path = tmp_path / "long.pdf"
    write_minimal_pdf(str(pdf_path), ["Body.", "Body two."], footers=["12345", "iv"])

    pages = extract_pages(str(pdf_path))

    assert [p.page_number_candidate for p in pages] == [None, None]
    assert "12345" in pages[0].text and "iv" in pages[1].text


def test_pages_without_footer_are_unchanged(tmp_path):
    pdf_path = tmp_path / "nofooter.pdf"
    write_minimal_pdf(str(pdf_path), ["Only body."])

    page = extract_pages(str(pdf_path))[0]

    assert page.text == "Only body." and page.page_number_candidate is None
