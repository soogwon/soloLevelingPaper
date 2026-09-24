"""사용자가 맥락을 선택하지 않은 요청에 기본 맥락을 준비하는 검색 진입점."""

from dataclasses import dataclass

from solo_leveling.domain.evidence_qa import SearchResult, positive_int, require_text, require_tuple
from .ports import DefaultContextStore, ScopedRetriever
from .search import ContextSearchService


@dataclass(frozen=True)
class ContextSearchResponse:
    """후속 질문·근거 저장에 사용할 맥락 ID와 검색 결과."""
    context_id: str
    result: SearchResult


class SearchEntryService:
    def __init__(self, contexts: DefaultContextStore, retriever: ScopedRetriever):
        self.contexts = contexts
        self.searcher = ContextSearchService(contexts, retriever)

    def search(self, question: str, *, context_id: str | None = None,
               version_id: str | None = None, top_k: int = 5,
               pdf_pages: tuple[int, ...] = (), section_ids: tuple[str, ...] = ()) -> ContextSearchResponse:
        """맥락 ID가 생략된 경우에만 기본 맥락을 생성한다. 잘못된 ID는 대체하지 않는다."""
        # 잘못된 요청 때문에 DB에 기본 맥락이 생성되지 않도록 먼저 검증한다.
        require_text(question, 'question')
        positive_int(top_k, 'top_k')
        require_tuple(pdf_pages, int, 'pdf_pages')
        require_tuple(section_ids, str, 'section_ids')
        for page in pdf_pages:
            positive_int(page, 'pdf_page')
        for section in section_ids:
            require_text(section, 'section_id')
        if len(set(pdf_pages)) != len(pdf_pages) or len(set(section_ids)) != len(section_ids):
            raise ValueError('검색 범위 조건에 중복 값이 있습니다.')
        if version_id is not None:
            require_text(version_id, 'version_id')
        if context_id is not None:
            require_text(context_id, 'context_id')
            context = self.contexts.get_context(context_id)
            if context is None:
                raise ValueError('학습 맥락을 찾을 수 없습니다.')
            if version_id is not None and context.version_id != version_id:
                raise ValueError('학습 맥락과 논문 버전이 일치하지 않습니다.')
        else:
            require_text(version_id, 'version_id')
            context = self.contexts.get_or_create_default_context(version_id)
            if context.version_id != version_id:
                raise ValueError('기본 학습 맥락과 논문 버전이 일치하지 않습니다.')
        result = self.searcher.search(question, context.context_id, top_k,
                                     pdf_pages=pdf_pages, section_ids=section_ids)
        return ContextSearchResponse(context.context_id, result)
