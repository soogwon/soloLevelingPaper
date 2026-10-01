"""
infrastructure/parsing — 페이지 경계를 넘지 않는 청킹

22번 문서: "청크가 페이지 경계를 넘기지 않고 section은 null을 허용한다."
즉, 하나의 청크가 두 페이지에 걸쳐 있으면 안 되므로 먼저 페이지별로 나누고
그 안에서만 chunk_size 단위로 분할한다.

2026-09-29 수정: 기존엔 문자 수로만 잘라서 단어 중간이 끊기는 문제가 있었다
(예: "much"가 "mu"/"ch"로 분리). 자를 위치 근처에서 문장 끝 → 공백 순으로
경계를 찾아 자르도록 바꿨다. 수식/URL처럼 공백 없는 긴 덩어리는 경계를 못
찾으므로 기존처럼 강제로 자른다(무한 루프 방지 목적의 예외 규칙).

2026-09-30 추가 수정: 위 수정은 각 청크의 "끝"만 경계에 맞췄을 뿐, overlap
만큼 되돌아간 "다음 청크의 시작"은 여전히 단순 뺄셈(end - overlap)이라
단어 중간에 떨어질 수 있었다(예: "learning"이 "lea"/"rning"으로 분리).
실측 검증(diag_chunk_boundary.py)으로 발견. next_start도 가장 가까운
공백 다음 위치로 스냅하도록 고쳤다.
"""
import re
import uuid
from typing import List, Optional

from solo_leveling.domain.models import Chunk
from solo_leveling.infrastructure.parsing.pdf_extractor import ExtractedPage

CHUNK_SIZE = 500
CHUNK_OVERLAP = 80

# end 지점에서 뒤로 거슬러 올라가며 경계를 찾을 최대 폭.
# 너무 크면 청크가 지나치게 짧아질 수 있어 chunk_size의 절반으로 제한한다.
_BOUNDARY_SEARCH_WINDOW_RATIO = 0.5

_SENTENCE_END_RE = re.compile(r"[.!?][\"')\]]?\s")
_WHITESPACE_RE = re.compile(r"\s")


def _find_break_point(text: str, start: int, end: int, chunk_size: int) -> Optional[int]:
    """[start, end) 구간 끝(end) 근처에서 자연스러운 경계 위치를 찾는다.

    문장 끝을 우선 찾고, 없으면 공백을 찾는다. 탐색 범위를 벗어나면(=경계를
    못 찾으면) None을 반환해 호출부가 기존처럼 강제로 자르게 한다.
    """
    window_start = max(start, end - int(chunk_size * _BOUNDARY_SEARCH_WINDOW_RATIO))

    best_sentence_end = None
    for match in _SENTENCE_END_RE.finditer(text, window_start, end):
        best_sentence_end = match.end()
    if best_sentence_end is not None:
        return best_sentence_end

    best_whitespace = None
    for match in _WHITESPACE_RE.finditer(text, window_start, end):
        best_whitespace = match.start()
    if best_whitespace is not None:
        return best_whitespace

    return None


def _find_next_boundary(text: str, pos: int, limit: int) -> int:
    """pos 이후 가장 가까운 공백을 찾아 그 다음 위치(=새 단어 시작)를 반환한다.

    limit 전에 공백을 못 찾으면(=공백 없는 긴 덩어리) pos를 그대로 반환해
    호출부가 기존처럼 그 자리에서 시작하게 한다(예외 규칙).
    """
    match = _WHITESPACE_RE.search(text, pos, limit)
    if match:
        return match.end()
    return pos


def _split_within_page(text: str, chunk_size: int, overlap: int) -> List[str]:
    text = text.strip()
    if not text:
        return []
    pieces = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            boundary = _find_break_point(text, start, end, chunk_size)
            if boundary is not None and boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= n:
            break
        next_start = end - overlap
        if next_start <= start:  # 경계 탐색으로 end가 많이 당겨진 경우 방지
            next_start = end
        else:
            # overlap만큼 되돌아간 지점이 단어 중간일 수 있으므로, 그 다음
            # 공백 뒤(=새 단어 시작)까지 앞으로 당긴다. end를 넘어서면 안 되므로
            # 탐색 상한은 end로 제한한다.
            next_start = _find_next_boundary(text, next_start, end)
            if next_start <= start:
                next_start = end
        start = next_start
    return pieces


def chunk_pages(
    parse_revision_id: str,
    pages: List[ExtractedPage],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
    id_factory: Optional[callable] = None,
) -> List[Chunk]:
    """
    페이지별로 독립적으로 청킹한다. 한 청크가 여러 페이지의 텍스트를
    엮어 만들어지는 일은 없다. 각 청크는 자신이 나온 pdf_page를 그대로 가진다.

    id_factory: 테스트에서 결정론적 ID를 주입하기 위한 것. 기본은 uuid4.
    """
    make_id = id_factory or (lambda: str(uuid.uuid4()))
    chunks: List[Chunk] = []
    global_index = 0

    for page in pages:
        pieces = _split_within_page(page.text, chunk_size, overlap)
        for piece in pieces:
            chunks.append(
                Chunk(
                    chunk_id=make_id(),
                    parse_revision_id=parse_revision_id,
                    chunk_index=global_index,
                    original_text=piece,
                    text=None,  # 번역 문구는 B 담당 파이프라인이 채운다
                    translation_revision_id=None,
                    printed_page_label=page.printed_page_label,
                    pdf_page=page.pdf_page,
                    section_id=None,
                )
            )
            global_index += 1

    return chunks
