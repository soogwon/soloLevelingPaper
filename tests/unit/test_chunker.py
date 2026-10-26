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


# ── 청크 경계 보증(#16) ──────────────────────────────────────
import random
import re

_WORDS = ["attention", "learning", "sequence", "positions", "transformer", "the", "of", "and",
          "relative", "a", "self-attention", "x_i", "(1)", "O(n^2·d)", "representation"]


def _random_text(seed: int, words: int, sentence_every: int = 0) -> str:
    rng = random.Random(seed)
    parts = []
    for i in range(words):
        word = rng.choice(_WORDS)
        if sentence_every and i % sentence_every == sentence_every - 1:
            word += "."
        parts.append(word)
    return " ".join(parts)


def _locate(text: str, pieces):
    """각 조각이 원문 어디에서 시작하는지(순서대로) 찾는다."""
    positions, cursor = [], 0
    for piece in pieces:
        start = text.find(piece, cursor)
        assert start >= 0, "조각이 원문에서 순서대로 발견되어야 한다"
        positions.append(start)
        cursor = start + 1
    return positions


def test_no_chunk_starts_or_ends_in_the_middle_of_a_word():
    """이슈 #16: 청크 끝과 겹침 시작점이 모두 단어 경계여야 한다(단어 중간 시작 0개)."""
    for seed in range(200):
        text = _random_text(seed, words=60 + seed * 3, sentence_every=(seed % 4) * 5)
        pieces = _split_within_page(text, chunk_size=500, overlap=80)
        for piece, start in zip(pieces, _locate(text, pieces)):
            end = start + len(piece)
            assert start == 0 or text[start - 1].isspace(), f"seed={seed}: 단어 중간에서 시작: {piece[:20]!r}"
            assert end == len(text) or text[end].isspace() or piece[-1] in ".!?", \
                f"seed={seed}: 단어 중간에서 끝남: {piece[-20:]!r}"


def test_overlap_is_still_applied_between_neighbouring_chunks():
    text = _random_text(7, words=400)
    pieces = _split_within_page(text, chunk_size=500, overlap=80)
    positions = _locate(text, pieces)
    overlaps = [positions[i] + len(pieces[i]) - positions[i + 1] for i in range(len(pieces) - 1)]
    assert all(o > 0 for o in overlaps), "이웃 청크는 서로 겹쳐야 한다"


def test_unbroken_text_without_spaces_is_still_split_without_looping():
    """공백이 전혀 없는 덩어리(수식·URL)는 경계를 못 찾으므로 강제로 자르되 반드시 끝난다."""
    pieces = _split_within_page("a" * 1200, chunk_size=500, overlap=80)
    assert len(pieces) >= 3 and all(len(p) <= 500 for p in pieces)


def test_chunk_pages_keeps_boundaries_with_sections():
    text = _random_text(3, words=300)
    pages = [ExtractedPage(pdf_page=1, text=text), ExtractedPage(pdf_page=2, text=text)]
    chunks = chunk_pages("parse-1", pages, id_factory=_id_gen())
    for chunk in chunks:
        assert not chunk.original_text[:1].isspace()
        first = chunk.original_text.split(" ", 1)[0].rstrip(".")
        assert first in _WORDS, f"잘린 단어로 시작: {first!r}"
