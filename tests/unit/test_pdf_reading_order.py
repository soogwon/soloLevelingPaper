"""서비스 추출부터 청킹까지 실제 합성 PDF의 읽기 순서를 확인한다."""

import pdfplumber
import pytest

from solo_leveling.infrastructure.parsing.pdf_extractor import extract_pages
from solo_leveling.infrastructure.parsing.chunker import chunk_pages
from solo_leveling.infrastructure.parsing.processing_parser import ProcessingPdfParser
from tests.fixtures.pdf_layout import build_layout_pdf


def text(x, y, value, size=10):
    return f'BT /F1 {size} Tf {x} {y} Td ({value}) Tj ET'


def columns():
    ops = [text(150, 740, 'A Shared Title That Spans Across The Whole Page Width Center')]
    ops += [text(50, 700, '1 Introduction', 14), text(330, 700, '2 Methods', 14)]
    for i in range(14):
        ops += [text(50, 680 - i * 16, f'Left sentence {i:02d}.'),
                text(330, 680 - i * 16, f'Right sentence {i:02d}.')]
    return '\n'.join(ops)


@pytest.mark.parametrize('footer', ['', '7'])
def test_two_columns_keep_order_headings_and_page_boundaries(tmp_path, footer):
    stream = columns()
    if footer:
        stream += '\n' + text(300, 40, footer)
    path = tmp_path / 'columns.pdf'
    path.write_bytes(build_layout_pdf([stream, text(50, 700, 'Next page.')]))
    pages = extract_pages(str(path))
    lines = pages[0].text.splitlines()
    assert lines[0] == 'A Shared Title That Spans Across The Whole Page Width Center'
    assert lines[1:16] == ['1 Introduction'] + [f'Left sentence {i:02d}.' for i in range(14)]
    assert lines[16:] == ['2 Methods'] + [f'Right sentence {i:02d}.' for i in range(14)]
    assert pages[0].page_number_candidate == (footer or None)
    assert pages[0].printed_page_label is None
    assert [h.title for h in pages[0].headings] == ['1 Introduction', '2 Methods']
    for heading in pages[0].headings:
        assert pages[0].text[heading.offset:].startswith(heading.title)
    chunks = chunk_pages('parse', pages)
    assert all('Next page' not in c.original_text for c in chunks if c.pdf_page == 1)
    assert all('Left sentence' not in c.original_text for c in chunks if c.pdf_page == 2)
    assert [p.pdf_page for p in pages] == [1, 2]


@pytest.mark.parametrize('kind', ['gap', 'single', 'caption', 'empty'])
def test_ambiguous_layout_preserves_existing_text(tmp_path, kind):
    if kind == 'gap':
        stream = '\n'.join(text(220, 700-i*16, f'Row {i:02d} left end') + '\n' +
                           text(308, 700-i*16, 'right continuation') for i in range(14))
    elif kind == 'single':
        stream = '\n'.join(text(330, 700-i*16, f'Short single line {i:02d}') for i in range(14))
    elif kind == 'caption':
        stream = columns() + '\n' + text(100, 300, 'Full width caption spans the center and goes onward')
    else:
        stream = ''
    path = tmp_path / 'fallback.pdf'
    path.write_bytes(build_layout_pdf([stream]))
    with pdfplumber.open(path) as document:
        expected = document.pages[0].extract_text(x_tolerance=1.5, y_tolerance=5) or ''
    assert extract_pages(str(path))[0].text == expected


def test_column_split_retains_rotation_exclusion_metrics(tmp_path):
    path = tmp_path / 'rotation.pdf'
    stamp = 'BT /F1 10 Tf 0 1 -1 0 20 300 Tm (ROTATED) Tj ET'
    path.write_bytes(build_layout_pdf([columns() + '\n' + stamp]))
    page = extract_pages(str(path))[0]
    assert 'ROTATED' not in page.text
    assert page.rotation_metrics.excluded_chars == 7
    assert page.text.index('Left sentence 13.') < page.text.index('Right sentence 00.')


def test_remote_memory_parser_keeps_section_contents_and_stable_ids():
    pdf = build_layout_pdf([columns() + '\n' + text(300, 40, '7')])
    parser = ProcessingPdfParser()
    document = parser.parse(pdf, 'new-parse')
    assert document == parser.parse(pdf, 'new-parse')
    by_title = {s.title: s.section_id for s in document.sections}
    left = [c for c in document.chunks if c.section_id == by_title['1 Introduction']]
    right = [c for c in document.chunks if c.section_id == by_title['2 Methods']]
    assert left and right
    assert all('Right sentence' not in c.original_text for c in left)
    assert all('Left sentence' not in c.original_text for c in right)
    assert 'Left sentence 13.' in '\n'.join(c.original_text for c in left)
    assert 'Right sentence 13.' in '\n'.join(c.original_text for c in right)
    assert all(c.pdf_page == 1 and c.printed_page_label is None for c in document.chunks)


def test_overlapping_header_boundary_preserves_characters(tmp_path):
    # 제목 바로 옆의 작은 글자가 수평 분할선을 걸치면 두 영역에 중복될 수 있다.
    stream = columns() + '\n' + text(50, 735, 'Boundary note')
    path = tmp_path / 'boundary.pdf'
    path.write_bytes(build_layout_pdf([stream]))
    with pdfplumber.open(path) as document:
        expected = document.pages[0].extract_text(x_tolerance=1.5, y_tolerance=5)
    assert extract_pages(str(path))[0].text == expected
