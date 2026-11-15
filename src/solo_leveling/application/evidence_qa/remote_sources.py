"""원격 저장 출처를 기존 검색·문장 보충 검사에 연결한다. DB adapter는 별도다."""

from dataclasses import dataclass
from typing import Protocol

from solo_leveling.domain.models import Chunk
from solo_leveling.domain.evidence_qa import SearchScope, SearchResult, RetrievedChunk, RetrievalMethod
from .continuation import supplement_continuations
from .remote_contract import (
    RemoteSearchBinding, RemoteSearchFilters, RemoteRetrievalContractError, validate_remote_hits,
)
from .validators import validate_search_result


@dataclass(frozen=True, repr=False)
class RemoteSourceChunk:
    binding: RemoteSearchBinding
    chunk_id: str
    ordinal: int
    pdf_page: int
    section_id: str | None
    original_text: str
    translation_text: str | None
    printed_page_label: str | None = None

    def to_chunk(self):
        if (not isinstance(self.binding, RemoteSearchBinding) or type(self.ordinal) is not int or self.ordinal < 0
                or not isinstance(self.chunk_id, str) or not self.chunk_id.strip()
                or type(self.pdf_page) is not int or self.pdf_page < 1
                or not isinstance(self.original_text, str) or not self.original_text.strip()
                or self.translation_text is not None and
                    (not isinstance(self.translation_text, str) or not self.translation_text.strip())):
            raise RemoteRetrievalContractError('REMOTE_SOURCE_INVALID')
        return Chunk(self.chunk_id, self.binding.parse_revision_id, self.ordinal, self.original_text,
                     text=self.translation_text, translation_revision_id=self.binding.translation_revision_id,
                     printed_page_label=self.printed_page_label, pdf_page=self.pdf_page, section_id=self.section_id)


class RemoteSourceReader(Protocol):
    def read_chunks(self, binding: RemoteSearchBinding, chunk_ids: tuple[str, ...]) -> tuple[RemoteSourceChunk, ...]:
        """한 읽기 snapshot에서 소속·게시 집합·저장 원문/번역을 확인한다. 최신본 대체 금지."""
        ...

    def following_chunks(self, binding: RemoteSearchBinding, ordinals: tuple[int, ...]) -> tuple[RemoteSourceChunk, ...]:
        """동일 출처의 인접 후보만 반환한다. 범위 밖 허용 구절은 기존 #108 검사에서 제한한다."""
        ...


@dataclass(frozen=True, repr=False)
class PinnedRemoteSearch:
    binding: RemoteSearchBinding
    search: SearchResult


def assemble_remote_sources(question, binding, filters, ranked_hits, reader: RemoteSourceReader):
    if not isinstance(ranked_hits, tuple):
        raise RemoteRetrievalContractError('REMOTE_SOURCE_INVALID')
    try:
        hits = tuple(r.hit for r in ranked_hits)
        if validate_remote_hits(binding, filters, hits, max(1, len(hits))) != ranked_hits:
            raise RemoteRetrievalContractError('REMOTE_SOURCE_INVALID')
        ids = tuple(hit.chunk_id for hit in hits)
        rows = reader.read_chunks(binding, ids) if ids else ()
        if not isinstance(rows, tuple) or any(not isinstance(r, RemoteSourceChunk) or r.binding != binding for r in rows):
            raise RemoteRetrievalContractError('REMOTE_SOURCE_SCOPE_MISMATCH')
        by_id = {r.chunk_id: r for r in rows}
        if len(by_id) != len(rows) or set(by_id) != set(ids):
            raise RemoteRetrievalContractError('REMOTE_SOURCE_IDS_MISMATCH')
        chunks = []
        for hit in hits:
            source = by_id[hit.chunk_id]
            if ((source.pdf_page, source.section_id) != (hit.pdf_page, hit.section_id)
                    or source.translation_text is None):
                raise RemoteRetrievalContractError('REMOTE_SOURCE_METADATA_MISMATCH')
            chunks.append(source.to_chunk())
        scope = SearchScope(binding.server_version_id, binding.parse_revision_id, binding.translation_revision_id,
                            filters.pdf_pages, filters.section_ids, binding.embedding_set_id)
        search = SearchResult(question, scope, RetrievalMethod.VECTOR, binding.embedding_set_id,
                              tuple(RetrievedChunk(c, r.score, r.rank) for c, r in zip(chunks, ranked_hits)))
        validate_search_result(search)
        return PinnedRemoteSearch(binding, search)
    except RemoteRetrievalContractError:
        raise
    except Exception:
        raise RemoteRetrievalContractError('REMOTE_SOURCE_UNAVAILABLE') from None


def supplement_remote_sources(pinned: PinnedRemoteSearch, filters: RemoteSearchFilters, reader: RemoteSourceReader):
    if not isinstance(pinned, PinnedRemoteSearch) or not isinstance(filters, RemoteSearchFilters):
        raise RemoteRetrievalContractError('REMOTE_SOURCE_SCOPE_MISMATCH')
    binding, search = pinned.binding, pinned.search
    if (search.scope.version_id != binding.server_version_id or search.scope.parse_revision_id != binding.parse_revision_id
            or search.scope.translation_revision_id != binding.translation_revision_id
            or search.embedding_set_id != binding.embedding_set_id or search.scope.pdf_pages != filters.pdf_pages
            or search.scope.section_ids != filters.section_ids):
        raise RemoteRetrievalContractError('REMOTE_SOURCE_SCOPE_MISMATCH')
    class Following:
        def following_chunks(self, current, indices):
            rows = reader.following_chunks(binding, indices)
            if not isinstance(rows, tuple) or any(not isinstance(r, RemoteSourceChunk) or r.binding != binding
                                                  or r.ordinal not in indices for r in rows):
                raise RemoteRetrievalContractError('REMOTE_SOURCE_SCOPE_MISMATCH')
            if len({r.chunk_id for r in rows}) != len(rows) or any(
                    r.chunk_id in {c.chunk_id for c in current.candidate_chunks} for r in rows):
                raise RemoteRetrievalContractError('REMOTE_SOURCE_IDS_MISMATCH')
            return tuple(r.to_chunk() for r in rows)
    try:
        result = supplement_continuations(search, Following(), allow_page_completion=not filters.strict_pages)
        return PinnedRemoteSearch(binding, result)
    except RemoteRetrievalContractError:
        raise
    except Exception:
        raise RemoteRetrievalContractError('REMOTE_SOURCE_UNAVAILABLE') from None
