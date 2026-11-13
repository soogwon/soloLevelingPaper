"""논문 큰 섹션 제목 인식과 청크의 section_id 배정(M1-05a, #65)을 검증한다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from tests.fixtures.pdf_builder import build_styled_pdf
from solo_leveling.infrastructure.parsing import pdf_extractor
from solo_leveling.infrastructure.parsing.chunker import chunk_pages, chunk_pages_with_sections
from solo_leveling.infrastructure.parsing.pdf_extractor import ExtractedPage, extract_pages
from solo_leveling.infrastructure.parsing.sections import (
    LineStyle, PageHeading, detect_headings, is_heading_line, make_section_id,
)

BODY = 10.0


def line(text, size=BODY, bold=False, top_ratio=None):
    return LineStyle(text, size, bold, top_ratio)


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


def test_freeform_unnumbered_heading_requires_explicit_document_context():
    candidate = line("Attention Visualizations", size=12.0)
    assert not is_heading_line(candidate, BODY)
    assert is_heading_line(candidate, BODY, allow_unnumbered=True)


@pytest.mark.parametrize('text', [
    'Figure 3: Attention Visualizations',
    'Fig. 1 Attention weights',
    'Table 2 Results',
    'Table IV Results',
    'Input-Input Layer5',
    'A large sentence that ends like ordinary prose.',
])
def test_freeform_heading_rejects_captions_diagram_labels_and_prose(text):
    assert not is_heading_line(line(text, size=13.0, bold=True), BODY, allow_unnumbered=True)


def test_repeated_header_and_preceding_figure_label_are_not_headings():
    text = _text('Input-Input Layer5', 'Attention Visualizations', 'Figure 3: caption')
    headings = detect_headings(text, [
        line('Input-Input Layer5', size=16.0, bold=True),
        line('Attention Visualizations', size=12.0),
        line('Figure 3: caption', size=10.0),
    ], BODY, allow_unnumbered=True, excluded_texts={'Conference 2026'})
    assert headings == [PageHeading('Attention Visualizations', 0)]


def test_repeated_line_filter_does_not_change_numbered_or_standard_heading_contract():
    text = _text('1 Introduction', 'References')
    headings = detect_headings(text, [
        line('1 Introduction', size=12.0),
        line('References', size=12.0),
    ], BODY, allow_unnumbered=True, excluded_texts={'1 introduction', 'references'})
    assert headings == [PageHeading('1 Introduction', 0),
                        PageHeading('References', text.index('References'))]


def test_repeated_margin_text_is_kept_when_it_is_a_heading_in_the_body():
    repeated = 'Attention Visualizations'
    text = _text(repeated, repeated, repeated)
    second = text.index(repeated, len(repeated))
    headings = detect_headings(text, [
        line(repeated, size=12.0, top_ratio=0.05),
        line(repeated, size=12.0, top_ratio=0.45),
        line(repeated, size=12.0, top_ratio=0.95),
    ], BODY, allow_unnumbered=True, excluded_texts={repeated})
    assert headings == [PageHeading(repeated, second)]


def test_extract_pages_excludes_only_repeated_margin_header(tmp_path):
    body = (10.0, False, 'Ordinary body text establishes the body font size.')
    filler = [body] * 8
    path = tmp_path / 'headers.pdf'
    path.write_bytes(build_styled_pdf([
        [(17.0, False, 'Paper Title'), body, body],
        [(12.0, True, 'Conference Header')] + filler
        + [(12.0, False, 'Attention Visualizations'), body],
        [(12.0, True, 'Conference Header'), body, body],
    ]))
    pages = extract_pages(str(path))
    titles = [heading.title for page in pages for heading in page.headings]
    assert 'Conference Header' not in titles
    assert 'Attention Visualizations' in titles


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


def test_section_catalog_keeps_titles_order_pages_and_chunk_ranges():
    first = _text("Abstract", "Summary")
    second = _text("More", "1 Introduction", "Intro", "2 Method", "Method body")
    pages = [
        ExtractedPage(1, first, headings=[PageHeading("Abstract", 0)]),
        ExtractedPage(2, second, headings=[
            PageHeading("1 Introduction", second.index("1 Introduction")),
            PageHeading("2 Method", second.index("2 Method")),
        ]),
        ExtractedPage(3, "Method continues"),
    ]
    chunks, sections = chunk_pages_with_sections("rev", pages, chunk_size=500, overlap=80)
    assert [(s.section_id, s.title, s.ordinal, s.level, s.parent_section_id,
             s.start_pdf_page, s.end_pdf_page) for s in sections] == [
        ("01-abstract", "Abstract", 1, 1, None, 1, 2),
        ("02-introduction", "1 Introduction", 2, 1, None, 2, 2),
        ("03-method", "2 Method", 3, 1, None, 2, 3),
    ]
    ranges = {s.section_id: (s.start_chunk_index, s.end_chunk_index) for s in sections}
    for section_id, expected in ranges.items():
        indexes = [c.chunk_index for c in chunks if c.section_id == section_id]
        assert expected == (min(indexes), max(indexes))


def test_section_without_body_keeps_nullable_chunk_range():
    page = ExtractedPage(1, "Abstract", headings=[PageHeading("Abstract", 0)])
    _, sections = chunk_pages_with_sections("rev", [page], chunk_size=500, overlap=80)
    assert sections[0].start_chunk_index == 0  # 제목 자체도 원문 청크에 포함된다


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


def test_attention_pdf_splits_visualizations_from_references():
    path = Path(__file__).resolve().parents[2] / 'test_data' / 'tmp_pdfs' / '075b9be9-5944-4e83-af4b-2add5dbdeb94.pdf'
    pages = extract_pages(str(path))
    titles = [heading.title for page in pages for heading in page.headings]
    assert 'Attention Visualizations' in titles
    assert 'Input-Input Layer5' not in titles
    chunks, sections = chunk_pages_with_sections('rev', pages)
    by_title = {section.title: section for section in sections}
    assert (by_title['References'].start_pdf_page, by_title['References'].end_pdf_page) == (10, 12)
    assert (by_title['Attention Visualizations'].start_pdf_page,
            by_title['Attention Visualizations'].end_pdf_page) == (13, 15)
    assert all(not section.title.lower().startswith(('figure ', 'table ')) for section in sections)
    by_page = {n: [c.original_text for c in chunks if c.pdf_page == n] for n in (5, 7, 15)}
    assert any('dk = dv = dmodel/h = 64' in c for c in by_page[5])
    assert any('β1 = 0.9, β2 = 0.98 and ϵ = 10−9' in c for c in by_page[7])
    assert not any(c.original_text.startswith(('section, but no', 'model.')) for c in chunks)
    assert 'Input-Input Layer5' not in pages[14].text
    assert any('Figure 5:' in c for c in by_page[15])
    assert chunks


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
