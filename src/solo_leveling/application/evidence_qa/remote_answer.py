"""원격 검색과 기존 답변 검사를 연결하는 저장 전 준비 단계. 제품 응답/저장이 아니다."""
from copy import deepcopy
from dataclasses import dataclass

from solo_leveling.domain.models import Evidence
from .answer import AnswerService, AnswerResponse
from .entry import ContextSearchResponse
from .remote_contract import RemoteSearchBinding, RemoteSearchFilters, RemoteRetrievalContractError
from .remote_sources import assemble_remote_sources, RemoteContinuationReader


@dataclass(frozen=True, repr=False)
class PreparedRemoteAnswer:
    """A의 lease/권한/출처 재검사와 원자 저장 전에는 공개 반환하면 안 되는 내부 값."""
    binding: RemoteSearchBinding
    response: AnswerResponse
    evidence: tuple[Evidence, ...]

    @property
    def persistence_status(self):
        return 'not_saved'


class RemoteAnswerPreparationService:
    def __init__(self, search, source_reader, generator, *, semantic_verification=None, id_factory=None):
        self.search, self.source_reader, self.generator = search, source_reader, generator
        self.semantic_verification, self.id_factory = semantic_verification, id_factory

    def prepare(self, question, context_id, *, filters=RemoteSearchFilters(), top_k=5):
        if not isinstance(filters, RemoteSearchFilters):
            raise RemoteRetrievalContractError('REMOTE_SEARCH_INPUT_INVALID')
        # 변경 가능한 상태는 호출 안에 두어 서로 다른 사용자/실행의 binding을 공유하지 않는다.
        binding = None
        pending = ()
        service = self

        class Search:
            def search(self, question, *, context_id, version_id=None, top_k=5, pdf_pages=(), section_ids=()):
                nonlocal binding
                if (pdf_pages, section_ids) != (filters.pdf_pages, filters.section_ids):
                    raise RemoteRetrievalContractError('REMOTE_SCOPE_MISMATCH')
                found = service.search.search(question, context_id, filters=filters, top_k=top_k)
                if ((binding is not None and found.binding != binding)
                        or version_id is not None and version_id != found.binding.server_version_id):
                    raise RemoteRetrievalContractError('REMOTE_SCOPE_MISMATCH')
                binding = found.binding
                pinned = assemble_remote_sources(question, binding, filters, found.hits, service.source_reader)
                return ContextSearchResponse(binding.context_id, pinned.search)

        class Following:
            def following_chunks(self, search, indices):
                return RemoteContinuationReader(binding, filters, service.source_reader).following_chunks(search, indices)

        class Buffer:
            def save(self, saved_context, evidence):
                nonlocal pending
                if binding is None or saved_context != binding.context_id:
                    raise RemoteRetrievalContractError('REMOTE_SCOPE_MISMATCH')
                pending = deepcopy(tuple(evidence))

        evaluator = AnswerService(Search(), self.generator, Buffer(), continuation_reader=Following(),
            semantic_verification=self.semantic_verification, id_factory=self.id_factory)
        response = evaluator.answer(question, context_id=context_id, top_k=top_k,
            pdf_pages=filters.pdf_pages, section_ids=filters.section_ids, strict_pages=filters.strict_pages)
        return PreparedRemoteAnswer(binding, response, pending)
