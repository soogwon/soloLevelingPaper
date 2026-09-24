"""학습 맥락을 검색 범위로 해석하고 검색 결과의 경계를 검증한다."""

from solo_leveling.domain.evidence_qa import SearchResult, SearchScope, positive_int, require_text
from .ports import LearningContextReader, ScopedRetriever
from .validators import validate_search_result


class ContextSearchService:
    def __init__(self, contexts: LearningContextReader, retriever: ScopedRetriever):
        self.contexts = contexts
        self.retriever = retriever

    def search(self, question: str, context_id: str, top_k: int = 5, *,
               pdf_pages: tuple[int, ...] = (), section_ids: tuple[str, ...] = ()) -> SearchResult:
        """고정된 번역·색인 안에서 페이지와 섹션 조건을 모두 만족하는 청크를 찾는다."""
        require_text(question, 'question')
        require_text(context_id, 'context_id')
        positive_int(top_k, 'top_k')
        context = self.contexts.get_context(context_id)
        if context is None:
            raise ValueError('학습 맥락을 찾을 수 없습니다.')
        require_text(context.translation_revision_id, 'translation_revision_id')
        require_text(context.embedding_set_id, 'embedding_set_id')
        scope = SearchScope(context.version_id, context.parse_revision_id,
                            context.translation_revision_id, pdf_pages, section_ids)
        result = self.retriever.search(question, scope, top_k)
        if result.scope != scope or result.query != question or result.embedding_set_id != context.embedding_set_id:
            raise ValueError('검색 결과가 요청한 학습 맥락과 일치하지 않습니다.')
        validate_search_result(result)
        ids = [item.chunk.chunk_id for item in result.items]
        if len(ids) != len(set(ids)) or len(ids) > top_k:
            raise ValueError('검색 결과의 청크 수 또는 중복 ID가 올바르지 않습니다.')
        if [item.rank for item in result.items] != list(range(1, len(ids) + 1)):
            raise ValueError('검색 순위는 1부터 연속이어야 합니다.')
        return result
