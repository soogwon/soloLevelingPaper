"""잘린 검색 청크마다 바로 다음 청크를 한 단계만 보충한다."""

from dataclasses import replace

from solo_leveling.domain.evidence_qa import SearchResult, positive_int
from .fragments import fragment_flags
from .ports import ContinuationReader
from .validators import validate_chunk, validate_search_result


def supplement_continuations(search: SearchResult, reader: ContinuationReader,
                            limit: int = 20) -> SearchResult:
    """검색 순위는 유지하고 범위 안의 다음 청크만 독립 후보로 추가한다."""
    positive_int(limit, 'max_supplemental_chunks')
    validate_search_result(search)
    if search.supplemental_chunks:
        raise ValueError('이미 보충된 결과를 다시 확장할 수 없습니다.')
    present = {chunk.chunk_id for chunk in search.candidate_chunks}
    indices = tuple(dict.fromkeys(item.chunk.chunk_index + 1 for item in search.items
                                 if fragment_flags(item.chunk.original_text)[1]))
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
        if chunk is not None and chunk.chunk_id not in present:
            additions.append(replace(chunk))
            present.add(chunk.chunk_id)
            if len(additions) == limit:
                break
    result = replace(search, supplemental_chunks=tuple(additions))
    validate_search_result(result)
    return result
