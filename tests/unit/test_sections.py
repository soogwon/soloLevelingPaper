"""논문 큰 섹션 제목 인식과 청크의 section_id 배정(M1-05a, #65)을 검증한다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.fixtures.pdf_builder import build_styled_pdf
from solo_leveling.infrastructure.parsing import pdf_extractor
from solo_leveling.infrastructure.parsing.chunker import chunk_pages
from solo_leveling.infrastructure.parsing.pdf_extractor import ExtractedPage, extract_pages
from solo_leveling.infrastructure.parsing.sections import (
    LineStyle, PageHeading, is_heading_line, make_section_id,
)

BODY = 10.0


def line(text, size=BODY, bold=False):
    return LineStyle(text, size, bold)


@pytest.mark.parametrize("text", [
    "Abstract", "1 Introduction", "1. Introduction", "2 Background",
    "3 Model Architecture", "II. Related Work", "5 Conclusion", "References",
    "서론", "2 관련 연구",
])
def test_big_headings_are_recognized(text):
    assert is_heading_line(line(text, size=12.0), BODY)


@pytest.mark.parametrize("text", [
    "3.1 Encoder and Decoder Stacks",          # 세부 제목은 다루지 않는다
    "Attention Is All You Need",               # 논문 제목: 번호도 표준 이름도 아니다
    "Figure 1: The Transformer model",         # 그림 캡션
    "1 2 3",                                   # 글자 없는 번호
    "5",                                       # 페이지 번호
    "1 this sentence starts in lowercase and keeps going on for a long time",
    "2 Training the model for twelve epochs on the full dataset, as described.",
])
def test_non_headings_are_rejected(text):
    assert not is_heading_line(line(text, size=12.0), BODY)


def test_body_sized_plain_text_is_not_a_heading_but_bold_is():
    assert not is_heading_line(line("1 Introduction"), BODY)
    assert is_heading_line(line("1 Introduction", bold=True), BODY)


def test_section_id_is_ordered_and_readable():
    assert make_section_id(1, "Abstract") == "01-abstract"
    assert make_section_id(3, "3 Model Architecture") == "03-model-architecture"
    assert make_section_id(2, "2. 관련 연구") == "02-관련-연구"
    assert make_section_id(4, "***") == "04-section"


def _text(*lines):
    return "\n".join(lines)


def test_chunks_follow_headings_and_carry_across_pages():
    page1_text = _text("Title block", "Abstract", "We study things.")
    page2_text = _text("More abstract text.", "1 Introduction", "Intro text here.")
    page3_text = _text("Intro continues on this page.")
    pages = [
        ExtractedPage(1, page1_text, headings=[PageHeading("Abstract", page1_text.index("Abstract"))]),
        ExtractedPage(2, page2_text, headings=[PageHeading("1 Introduction", page2_text.index("1 Introduction"))]),
        ExtractedPage(3, page3_text),
    ]
    chunks = chunk_pages("p", pages, chunk_size=500, overlap=80)
    got = [(c.pdf_page, c.section_id, c.original_text.split("\n")[0]) for c in chunks]
    assert got == [
        (1, None, "Title block"),                 # 첫 제목 앞은 비워 둔다
        (1, "01-abstract", "Abstract"),
        (2, "01-abstract", "More abstract text."),
        (2, "02-introduction", "1 Introduction"),
        (3, "02-introduction", "Intro continues on this page."),
    ]


def test_pages_without_headings_chunk_exactly_as_before():
    pages = [ExtractedPage(1, "가" * 600), ExtractedPage(2, "나" * 600)]
    chunks = chunk_pages("p", pages, chunk_size=500, overlap=80)
    assert chunks and all(c.section_id is None for c in chunks)
    assert [c.original_text for c in chunks] == [
        c.original_text for c in chunk_pages("p", [ExtractedPage(1, "가" * 600), ExtractedPage(2, "나" * 600)],
                                             chunk_size=500, overlap=80)]


def test_invalid_heading_offsets_are_ignored():
    page = ExtractedPage(1, "some text", headings=[PageHeading("X", 999), PageHeading("Y", -5)])
    assert [c.section_id for c in chunk_pages("p", [page])] == [None]


def test_extract_pages_finds_headings_in_a_real_pdf(tmp_path):
    body = (10.0, False, "Body text line one of the paper goes here for a while.")
    pdf = build_styled_pdf([
        [(17.0, False, "A Study of Things"), (10.0, False, "Some Author"),
         (12.0, True, "Abstract"), body, body],
        [(12.0, True, "1 Introduction"), body, body,
         (10.0, True, "1.1 A subsection"), body,
         (12.0, True, "2 Method"), body, body],
        [body, body, (12.0, True, "References"), body],
    ])
    path = tmp_path / "paper.pdf"
    path.write_bytes(pdf)
    pages = extract_pages(str(path))
    titles = [[h.title for h in p.headings] for p in pages]
    assert titles == [["Abstract"], ["1 Introduction", "2 Method"], ["References"]]
    for page in pages:
        for heading in page.headings:
            assert page.text[heading.offset:].startswith(heading.title)
    ids = [c.section_id for c in chunk_pages("p", pages)]
    assert ids[0] is None
    assert set(ids) == {None, "01-abstract", "02-introduction", "03-method", "04-references"}


def test_pdf_without_recognizable_headings_still_extracts(tmp_path):
    body = (10.0, False, "Just a plain page with no headings at all.")
    path = tmp_path / "plain.pdf"
    path.write_bytes(build_styled_pdf([[body, body], [body]]))
    pages = extract_pages(str(path))
    assert len(pages) == 2 and all(p.headings == [] for p in pages)
    assert all(c.section_id is None for c in chunk_pages("p", pages))


def test_heading_detection_failure_does_not_break_text_extraction(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(build_styled_pdf([[(12.0, True, "Abstract"), (10.0, False, "Body text.")]]))

    def boom(page):
        raise RuntimeError("style failure")

    monkeypatch.setattr(pdf_extractor, "_line_styles", boom)
    pages = extract_pages(str(path))
    assert "Body text." in pages[0].text and pages[0].headings == []
