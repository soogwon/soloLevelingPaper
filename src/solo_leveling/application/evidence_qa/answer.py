"""검색·claim 구조 검증·답변 조립·근거 저장을 연결한다. 사실성 검증은 하지 않는다."""

from dataclasses import dataclass, replace
from typing import Callable
from uuid import uuid4

from solo_leveling.domain.evidence_qa import (
    AnswerResult, AnswerStatus, Claim, EvidenceInput, GeneratedAnswerDraft, GeneratedClaim,
    ReasonCode, SearchResult, positive_int, require_text,
)
from solo_leveling.domain.models import Evidence
from .entry import SearchEntryService
from .ports import ClaimGenerator, EvidenceWriter, GenerationUnavailable
from .response_parser import GenerationFormatError
from .errors import InvalidArgumentError
from .validators import citation_from_evidence, validate_answer_against_search, validate_search_result


class AnswerGenerationError(RuntimeError):
    """생성 제공자 장애로 답변을 만들지 못한 경우. 근거 부족과 구분한다."""


@dataclass(frozen=True)
class AnswerResponse:
    """사용한 맥락과 검증 기준을 함께 보존하는 답변 결과."""
    context_id: str
    search: SearchResult
    result: AnswerResult


class AnswerService:
    def __init__(self, search: SearchEntryService, generator: ClaimGenerator,
                 evidence_writer: EvidenceWriter, *, id_factory: Callable[[], str] | None = None,
                 max_expanded_top_k: int = 20):
        positive_int(max_expanded_top_k, 'max_expanded_top_k')
        self.search = search
        self.generator = generator
        self.evidence_writer = evidence_writer
        self.id_factory = id_factory or (lambda: str(uuid4()))
        self.max_expanded_top_k = max_expanded_top_k

    def answer(self, question: str, *, context_id: str | None = None,
               version_id: str | None = None, top_k: int = 5,
               pdf_pages: tuple[int, ...] = (), section_ids: tuple[str, ...] = ()) -> AnswerResponse:
        try:
            positive_int(top_k, 'top_k')
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from None
        found = self.search.search(question, context_id=context_id, version_id=version_id,
                                   top_k=top_k, pdf_pages=pdf_pages, section_ids=section_ids)
        search = self._snapshot(found.result)
        allocated = set()
        response = self._answer_from_search(found.context_id, search, allocated)
        expanded_k = min(top_k * 2, self.max_expanded_top_k)
        # 정상적인 빈 초안만 추가 검색한다. 오류·형식 실패·이미 소진한 범위는 재시도하지 않는다.
        if (response.result.reason_code != ReasonCode.EVIDENCE_NOT_FOUND
                or not search.items or len(search.items) < top_k or expanded_k <= top_k):
            return response

        expanded = self.search.search(question, context_id=found.context_id,
            version_id=search.scope.version_id, top_k=expanded_k,
            pdf_pages=search.scope.pdf_pages, section_ids=search.scope.section_ids)
        expanded_search = self._snapshot(expanded.result)
        if (expanded.context_id != found.context_id or expanded_search.scope != search.scope
                or expanded_search.query != search.query
                or expanded_search.embedding_set_id != search.embedding_set_id
                or expanded_search.retrieval_method != search.retrieval_method):
            raise ValueError('추가 검색이 최초 검색의 맥락·범위·색인과 일치하지 않습니다.')
        previous = {item.chunk.chunk_id: item.chunk for item in search.items}
        current = {item.chunk.chunk_id: item.chunk for item in expanded_search.items}
        if any(current.get(key) != chunk for key, chunk in previous.items()):
            raise ValueError('추가 검색 중 기존 근거가 변경되거나 누락되었습니다.')
        if not current.keys() - previous.keys():
            return response
        # 새 청크가 추가된 경우에만 전체 확장 근거로 한 번 더 생성한다. 저장은 성공한 최종 근거만 한다.
        return self._answer_from_search(found.context_id, expanded_search, allocated)

    @staticmethod
    def _snapshot(result: SearchResult) -> SearchResult:
        # 가변 Chunk를 복사해 검색 시점의 본문·페이지를 답변 조립 동안 보존한다.
        search = replace(result, items=tuple(replace(item, chunk=replace(item.chunk))
                                             for item in result.items))
        validate_search_result(search)
        return search

    def _answer_from_search(self, context_id: str, search: SearchResult,
                            allocated: set[str]) -> AnswerResponse:

        def insufficient(reason, message):
            result = AnswerResult(AnswerStatus.INSUFFICIENT_EVIDENCE, message, (), (), reason)
            validate_answer_against_search(result, search)
            return AnswerResponse(context_id, search, result)

        if not search.items:
            return insufficient(ReasonCode.EVIDENCE_NOT_FOUND, '선택한 범위에서 답변에 사용할 근거를 찾지 못했습니다.')
        require_text(search.scope.translation_revision_id, 'translation_revision_id')
        inputs, evidence_by_id = [], {}
        chunk_ids = set()

        def new_id():
            value = self.id_factory()
            require_text(value, 'generated_id')
            if value in allocated:
                raise ValueError('발급된 ID가 중복됩니다.')
            allocated.add(value)
            return value

        for item in search.items:
            chunk = item.chunk
            if chunk.chunk_id in chunk_ids:
                raise ValueError('검색 청크가 중복됩니다.')
            chunk_ids.add(chunk.chunk_id)
            require_text(chunk.text, 'text_ko')
            evidence_id = new_id()
            inputs.append(EvidenceInput(evidence_id, chunk.chunk_id, chunk.text,
                                        chunk.original_text, chunk.printed_page_label, chunk.pdf_page))
            # 현재는 청크 전체를 인용한다. 문장 단위 발췌와 내용 검증은 후속 작업이다.
            evidence_by_id[evidence_id] = Evidence(evidence_id, chunk.chunk_id, chunk.text, chunk.original_text)
        try:
            draft = self.generator.generate_claims(search.query, tuple(inputs))
        except GenerationFormatError:
            return insufficient(ReasonCode.VERIFICATION_FAILED, '생성된 답변의 응답 형식을 확인하지 못했습니다.')
        except GenerationUnavailable:
            raise AnswerGenerationError('답변 생성 서비스를 사용할 수 없습니다.') from None

        try:
            if not isinstance(draft, GeneratedAnswerDraft):
                raise ValueError('생성 결과 형식이 올바르지 않습니다.')
            draft = GeneratedAnswerDraft(draft.claims)
            if not draft.claims:
                return insufficient(ReasonCode.EVIDENCE_NOT_FOUND, '검색된 근거로 답변 초안을 구성하지 못했습니다.')
            for claim in draft.claims:
                GeneratedClaim(claim.text, claim.evidence_ids)
                if (not claim.evidence_ids or len(set(claim.evidence_ids)) != len(claim.evidence_ids)
                        or not set(claim.evidence_ids).issubset(evidence_by_id)):
                    raise ValueError('주장이 제공된 근거를 올바르게 참조하지 않습니다.')
        except (ValueError, TypeError, AttributeError):
            return insufficient(ReasonCode.VERIFICATION_FAILED, '생성된 답변의 근거 연결을 확인하지 못했습니다.')

        claims = tuple(Claim(new_id(), claim.text, claim.evidence_ids) for claim in draft.claims)
        used_ids = dict.fromkeys(eid for claim in claims for eid in claim.evidence_ids)
        used = tuple(evidence_by_id[eid] for eid in used_ids)
        citations = tuple(citation_from_evidence(evidence, search) for evidence in used)
        result = AnswerResult(AnswerStatus.OK, '\n'.join(c.text for c in claims), claims, citations, None)
        validate_answer_against_search(result, search)
        # 저장 성공 전에는 성공 응답을 반환하지 않는다. DB 오류는 숨기지 않는다.
        self.evidence_writer.save(context_id, used)
        return AnswerResponse(context_id, search, result)
