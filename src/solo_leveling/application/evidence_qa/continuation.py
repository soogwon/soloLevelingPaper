"""검색 청크 뒤의 미완결 연결과 선택적 겹침 문맥을 한 단계만 보충한다."""

from dataclasses import replace

from solo_leveling.domain.evidence_qa import SearchResult, positive_int
from .fragments import fragment_flags, evidence_body, sentence_ends
from .ports import ContinuationReader
from .validators import validate_chunk, validate_search_result


def supplement_continuations(search: SearchResult, reader: ContinuationReader,
                            limit: int = 20, *, include_overlapping_neighbors: bool = False) -> SearchResult:
    """검색 순위는 유지하고 범위 안의 다음 청크만 독립 후보로 추가한다."""
    positive_int(limit, 'max_supplemental_chunks')
    validate_search_result(search)
    if search.supplemental_chunks:
        raise ValueError('이미 보충된 결과를 다시 확장할 수 없습니다.')
    present = {chunk.chunk_id for chunk in search.candidate_chunks}
    anchors = {item.chunk.chunk_index + 1: item.chunk for item in search.items}
    incomplete = tuple(index for index, chunk in anchors.items()
                       if fragment_flags(chunk.original_text)[1])
    # 미완결 보충을 우선하고, 선택적으로 완결 청크 뒤의 겹침 문맥을 조회한다.
    indices = incomplete + tuple(index for index in anchors
        if include_overlapping_neighbors and index not in incomplete)
    if not indices:
        return search
    by_index = {}
    for chunk in reader.following_chunks(search, indices):
        validate_chunk(chunk, search.scope)
        if chunk.chunk_index not in indices or chunk.chunk_index in by_index:
            raise ValueError('보충 청크 번호가 요청과 다르거나 중복됩니다.')
        by_index[chunk.chunk_index] = chunk
    additions = []
    for index in indices:
        chunk = by_index.get(index)
        if (chunk is not None and index not in incomplete
                and not _has_overlapping_context(anchors[index].original_text, chunk.original_text)):
            continue
        if chunk is not None and chunk.chunk_id not in present:
            additions.append(replace(chunk))
            present.add(chunk.chunk_id)
            if len(additions) == limit:
                break
    result = replace(search, supplemental_chunks=tuple(additions))
    validate_search_result(result)
    return result


def _has_overlapping_context(previous: str, following: str) -> bool:
    """충분한 단어 단위 겹침 뒤에 새 완결 문장이 있는 다음 청크만 허용한다."""
    left, right = evidence_body(previous), evidence_body(following)
    # 짧은 관용구나 단어 일부의 우연한 일치를 문맥 연결로 간주하지 않는다.
    for start in range(len(left)):
        if start and not left[start - 1].isspace():
            continue
        overlap = left[start:]
        if len(overlap) < 20 or len(overlap.split()) < 4:
            continue
        if (right.startswith(overlap) and len(right) > len(overlap)
                and right[len(overlap)].isspace()
                and any(end > len(overlap) for end in sentence_ends(right))):
            return True
    return False
