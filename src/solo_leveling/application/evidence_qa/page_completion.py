"""페이지 밖 보충은 검색된 미완결 문장의 첫 종료 지점까지만 허용한다."""

import re

from .fragments import evidence_body, fragment_flags, sentence_ends


def _has_boundary_link(previous: str, following: str) -> bool:
    """겹침 또는 제한된 명사구 연결만 허용한다. 일반적인 의미 연결 판정은 아니다."""
    left, right = evidence_body(previous), evidence_body(following)
    # 충분한 단어 단위 겹침은 페이지 간 텍스트 연속성의 근거로 사용한다.
    for match in re.finditer(r'\S+', left):
        suffix = left[match.start():]
        if (len(suffix) >= 20 and len(suffix.split()) >= 4
                and right.startswith(suffix) and len(right) > len(suffix)
                and right[len(suffix)].isspace()):
            return True
    # 겹침 없는 경우는 검증한 명사구만 지원한다. 미지원 연결은 추측하지 않는다.
    pairs = {('sequence', 'length'), ('path', 'length'),
             ('representation', 'dimensionality')}
    last = re.search(r'\b([A-Za-z]+)$', left)
    first = re.match(r'([a-z]+)\b', right)
    return bool(last and first and (last[1].lower(), first[1]) in pairs)


def completion_excerpt(search, chunk):
    """허용된 인접 원문 발췌를 반환한다. 저장 청크와 번역문은 변경하지 않는다."""
    if not search.allow_page_completion or not search.scope.pdf_pages:
        return None
    if chunk.pdf_page != max(search.scope.pdf_pages) + 1 or not (chunk.section_id or "").strip():
        return None
    if (chunk.parse_revision_id != search.scope.parse_revision_id
            or chunk.translation_revision_id != search.scope.translation_revision_id):
        return None
    if search.scope.section_ids and chunk.section_id not in search.scope.section_ids:
        return None
    anchors = [item.chunk for item in search.items
               if item.chunk.chunk_index + 1 == chunk.chunk_index
               and item.chunk.pdf_page + 1 == chunk.pdf_page
               and item.chunk.section_id == chunk.section_id
               and fragment_flags(item.chunk.original_text)[1]]
    if (len(anchors) != 1 or not fragment_flags(chunk.original_text)[0]
            or not _has_boundary_link(anchors[0].original_text, chunk.original_text)):
        return None
    ends = sentence_ends(chunk.original_text, outside_page=True)
    if not ends:
        return None
    excerpt = chunk.original_text[:ends[0]]
    # A retrieved chunk has no layout metadata. Reject visible block boundaries.
    if re.search(r'(?im)^\s*(?:table|figure|fig\.)\s+\d|\n[ \t]*\n|\t|\|', excerpt):
        return None
    return excerpt
