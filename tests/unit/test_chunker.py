import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from solo_leveling.infrastructure.parsing.chunker import chunk_pages, _split_within_page
from solo_leveling.infrastructure.parsing.pdf_extractor import ExtractedPage


def _id_gen():
    counter = {"n": 0}

    def factory():
        counter["n"] += 1
        return f"chunk-{counter['n']}"

    return factory


def test_chunk_never_crosses_page_boundary():
    """22번 문서 핵심 요구사항: 첫 청크는 페이지를 넘기지 않는다"""
    pages = [
        ExtractedPage(pdf_page=1, text="가" * 600),  # chunk_size(500) 넘는 긴 텍스트
        ExtractedPage(pdf_page=2, text="나" * 600),
    ]
    chunks = chunk_pages("parse-1", pages, chunk_size=500, overlap=80, id_factory=_id_gen())

    page1_chunks = [c for c in chunks if c.pdf_page == 1]
    page2_chunks = [c for c in chunks if c.pdf_page == 2]

    assert len(page1_chunks) >= 1
    assert len(page2_chunks) >= 1
    # 페이지1 청크에 페이지2 글자가 섞이지 않아야 함
    assert all("나" not in c.original_text for c in page1_chunks)
    assert all("가" not in c.original_text for c in page2_chunks)


def test_chunk_preserves_pdf_page_metadata():
    pages = [ExtractedPage(pdf_page=1, text="short text"), ExtractedPage(pdf_page=5, text="another page")]
    chunks = chunk_pages("parse-1", pages, id_factory=_id_gen())

    pdf_pages_used = {c.pdf_page for c in chunks}
    assert pdf_pages_used == {1, 5}


def test_chunk_index_is_globally_sequential():
    pages = [ExtractedPage(pdf_page=1, text="가" * 600), ExtractedPage(pdf_page=2, text="나" * 600)]
    chunks = chunk_pages("parse-1", pages, chunk_size=500, overlap=80, id_factory=_id_gen())

    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks)))


def test_chunk_text_is_none_before_translation():
    pages = [ExtractedPage(pdf_page=1, text="원문입니다")]
    chunks = chunk_pages("parse-1", pages, id_factory=_id_gen())

    assert chunks[0].text is None
    assert chunks[0].original_text == "원문입니다"


def test_empty_page_produces_no_chunks():
    pages = [ExtractedPage(pdf_page=1, text=""), ExtractedPage(pdf_page=2, text="real content")]
    chunks = chunk_pages("parse-1", pages, id_factory=_id_gen())

    assert all(c.pdf_page != 1 for c in chunks)
    assert any(c.pdf_page == 2 for c in chunks)


def test_split_within_page_respects_overlap():
    pieces = _split_within_page("a" * 1000, chunk_size=500, overlap=80)
    assert len(pieces) >= 2
    assert all(len(p) <= 500 for p in pieces)
