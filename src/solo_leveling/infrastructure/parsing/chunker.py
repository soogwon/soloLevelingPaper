"""
infrastructure/parsing — 페이지 경계를 넘지 않는 청킹

22번 문서: "첫 청크는 페이지를 넘기지 않고 section은 null을 허용한다."
즉 하나의 청크가 두 페이지에 걸쳐 있으면 안 된다 — 페이지별로 먼저 나누고,
그 안에서만 chunk_size 단위로 분할한다.
"""
import uuid
from typing import List, Optional

from solo_leveling.domain.models import Chunk
from solo_leveling.infrastructure.parsing.pdf_extractor import ExtractedPage

CHUNK_SIZE = 500
CHUNK_OVERLAP = 80


def _split_within_page(text: str, chunk_size: int, overlap: int) -> List[str]:
    text = text.strip()
    if not text:
        return []
    pieces = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end == len(text):
            break
        start = end - overlap
    return pieces


def chunk_pages(
    parse_revision_id: str,
    pages: List[ExtractedPage],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
    id_factory: Optional[callable] = None,
) -> List[Chunk]:
    """
    페이지별로 독립적으로 청킹한다 — 청크 하나가 여러 페이지의 텍스트를
    섞어 담는 일이 없다. 각 청크는 자신이 나온 pdf_page를 그대로 가진다.

    id_factory: 테스트에서 결정론적 ID를 주입하기 위한 훅. 기본은 uuid4.
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
                    text=None,  # 번역 전 — B 담당 파이프라인이 채운다
                    translation_revision_id=None,
                    printed_page_label=page.printed_page_label,
                    pdf_page=page.pdf_page,
                    section_id=None,
                )
            )
            global_index += 1

    return chunks
