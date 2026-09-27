"""검색·claim 구조 검증·답변 조립·근거 저장을 연결한다. 사실성 검증은 하지 않는다."""

from dataclasses import dataclass, replace
from typing import Callable
from uuid import uuid4

from solo_leveling.domain.evidence_qa import (
    AnswerResult, AnswerStatus, Claim, EvidenceInput, GeneratedAnswerDraft, GeneratedClaim,
    ReasonCode, SearchResult, require_text,
)
from solo_leveling.domain.models import Evidence
from .entry import SearchEntryService
from .ports import ClaimGenerator, EvidenceWriter, GenerationUnavailable
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
                 evidence_writer: EvidenceWriter, *, id_factory: Callable[[], str] | None = None):
        self.search = search
        self.generator = generator
        self.evidence_writer = evidence_writer
        self.id_factory = id_factory or (lambda: str(uuid4()))

    def answer(self, question: str, *, context_id: str | None = None,
               version_id: str | None = None, top_k: int = 5,
               pdf_pages: tuple[int, ...] = (), section_ids: tuple[str, ...] = ()) -> AnswerResponse:
        found = self.search.search(question, context_id=context_id, version_id=version_id,
                                   top_k=top_k, pdf_pages=pdf_pages, section_ids=section_ids)
        # 가변 Chunk를 복사해 검색 시점의 본문·페이지를 답변 조립 동안 보존한다.
        search = replace(found.result, items=tuple(replace(item, chunk=replace(item.chunk))
                                                  for item in found.result.items))
        validate_search_result(search)

        def insufficient(reason, message):
            result = AnswerResult(AnswerStatus.INSUFFICIENT_EVIDENCE, message, (), (), reason)
            validate_answer_against_search(result, search)
            return AnswerResponse(found.context_id, search, result)

        if not search.items:
            return insufficient(ReasonCode.EVIDENCE_NOT_FOUND, '선택한 범위에서 답변에 사용할 근거를 찾지 못했습니다.')
        require_text(search.scope.translation_revision_id, 'translation_revision_id')
        inputs, evidence_by_id = [], {}
        chunk_ids = set()
        allocated = set()

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
        self.evidence_writer.save(found.context_id, used)
        return AnswerResponse(found.context_id, search, result)
