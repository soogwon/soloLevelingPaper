"""검색·claim 구조 검증·답변 조립·근거 저장을 연결한다. 사실성 검증은 하지 않는다."""

from dataclasses import dataclass, replace
from typing import Callable
from uuid import uuid4
from solo_leveling.diagnostics import traced, stage, evidence_diagnostic

from solo_leveling.domain.evidence_qa import (
    AnswerResult, AnswerStatus, Claim, EvidenceInput, GeneratedAnswerDraft, GeneratedClaim,
    ReasonCode, SearchResult, positive_int, require_text,
)
from solo_leveling.domain.models import Evidence
from .entry import SearchEntryService
from .ports import ClaimGenerator, ContinuationReader, ContinuationRepairTarget, EvidenceWriter, GenerationUnavailable
from .continuation import supplement_continuations
from .response_parser import GenerationFormatError
from .errors import InvalidArgumentError
from .fragments import fragment_flags
from .evidence_quality import assess_extraction_risk, ExtractionReason
from .validators import citation_from_evidence, validate_answer_against_search, validate_search_result


class AnswerGenerationError(RuntimeError):
    """생성 제공자 장애로 답변을 만들지 못한 경우. 근거 부족과 구분한다."""


@dataclass(frozen=True)
class AnswerResponse:
    """사용한 맥락과 검증 기준을 함께 보존하는 답변 결과."""
    context_id: str
    search: SearchResult
    result: AnswerResult


@dataclass
class _RepairBudget:
    used: bool = False


class _DraftValidationError(ValueError):
    """본문·알 수 없는 ID를 보관하지 않고 고정 코드와 주장 번호만 전달한다."""
    def __init__(self, code: str, claim_number: int):
        self.code = code
        self.claim_number = claim_number
        super().__init__(code)


def _validate_draft(draft, evidence_by_id):
    """최초 초안과 보완 초안을 같은 규칙으로 검사한다."""
    if not isinstance(draft, GeneratedAnswerDraft):
        raise ValueError('생성 결과 형식이 올바르지 않습니다.')
    draft = GeneratedAnswerDraft(draft.claims)
    for number, claim in enumerate(draft.claims, 1):
        GeneratedClaim(claim.text, claim.evidence_ids, claim.supports)
        if not claim.evidence_ids:
            raise _DraftValidationError('EMPTY_EVIDENCE_IDS', number)
        if len(set(claim.evidence_ids)) != len(claim.evidence_ids):
            raise _DraftValidationError('DUPLICATE_EVIDENCE_ID', number)
        if not set(claim.evidence_ids).issubset(evidence_by_id):
            raise _DraftValidationError('UNKNOWN_EVIDENCE_ID', number)
        if any(s.evidence_id not in claim.evidence_ids for s in claim.supports):
            raise _DraftValidationError('SUPPORT_REFERENCE_INVALID', number)
    return draft


class AnswerService:
    def __init__(self, search: SearchEntryService, generator: ClaimGenerator,
                 evidence_writer: EvidenceWriter, *, id_factory: Callable[[], str] | None = None,
                 max_expanded_top_k: int = 20, continuation_reader: ContinuationReader | None = None,
                 max_supplemental_chunks: int = 20):
        positive_int(max_expanded_top_k, 'max_expanded_top_k')
        positive_int(max_supplemental_chunks, 'max_supplemental_chunks')
        self.continuation_reader = continuation_reader
        self.max_supplemental_chunks = max_supplemental_chunks
        self.search = search
        self.generator = generator
        self.evidence_writer = evidence_writer
        self.id_factory = id_factory or (lambda: str(uuid4()))
        self.max_expanded_top_k = max_expanded_top_k

    @traced('answer', request=True)
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
        repair_budget = _RepairBudget()
        response = self._answer_from_search(found.context_id, search, allocated, repair_budget)
        expanded_k = min(top_k * 2, self.max_expanded_top_k)
        # 정상적인 빈 초안만 추가 검색한다. 오류·형식 실패·이미 소진한 범위는 재시도하지 않는다.
        if (repair_budget.used or response.result.reason_code != ReasonCode.EVIDENCE_NOT_FOUND
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
        return self._answer_from_search(found.context_id, expanded_search, allocated, repair_budget)

    @staticmethod
    def _snapshot(result: SearchResult) -> SearchResult:
        # 가변 Chunk를 복사해 검색 시점의 본문·페이지를 답변 조립 동안 보존한다.
        search = replace(result, items=tuple(replace(item, chunk=replace(item.chunk))
                                             for item in result.items),
                         supplemental_chunks=tuple(replace(c) for c in result.supplemental_chunks))
        validate_search_result(search)
        return search

    def _answer_from_search(self, context_id: str, search: SearchResult,
                            allocated: set[str], repair_budget: _RepairBudget) -> AnswerResponse:
        # 추가 검색 후 재생성하더라도 후보와 판정이 섞이지 않도록 시도를 구분한다.
        attempt_id = uuid4().hex

        def log_verification_failure(error, round_number):
            code, number = 'DRAFT_INVALID', None
            if isinstance(error, GenerationFormatError):
                code = error.code.value
            elif isinstance(error, _DraftValidationError):
                code, number = error.code, error.claim_number
            evidence_diagnostic('verification_failure', attempt_id=attempt_id,
                round_number=round_number, reason_code=code, claim_number=number)

        if self.continuation_reader is not None and search.items:
            search = supplement_continuations(search, self.continuation_reader,
                                              self.max_supplemental_chunks)

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

        for chunk in search.candidate_chunks:
            if chunk.chunk_id in chunk_ids:
                raise ValueError('검색 청크가 중복됩니다.')
            chunk_ids.add(chunk.chunk_id)
            require_text(chunk.text, 'text_ko')
            evidence_id = new_id()
            starts_mid, ends_mid = fragment_flags(chunk.original_text)
            inputs.append(EvidenceInput(evidence_id, chunk.chunk_id, chunk.text,
                                        chunk.original_text, chunk.printed_page_label, chunk.pdf_page,
                                        starts_mid, ends_mid))
            # 현재는 청크 전체를 인용한다. 문장 단위 발췌와 내용 검증은 후속 작업이다.
            evidence_by_id[evidence_id] = Evidence(evidence_id, chunk.chunk_id, chunk.text, chunk.original_text)
        # 검색 순서와 문서 순서는 다르므로, 이어지는 근거 관계를 명시적으로 전달한다.
        by_index = {chunk.chunk_index: item for chunk, item in zip(search.candidate_chunks, inputs)}
        inputs = [replace(item, follows_evidence_id=previous.evidence_id)
                  if (previous := by_index.get(chunk.chunk_index - 1)) is not None
                  and previous.ends_mid_sentence else item
                  for chunk, item in zip(search.candidate_chunks, inputs)]
        supplemental_ids = {chunk.chunk_id for chunk in search.supplemental_chunks}
        for item in inputs:
            evidence_diagnostic('candidate', attempt_id=attempt_id,
                evidence_id=item.evidence_id, chunk_id=item.chunk_id, pdf_page=item.pdf_page,
                follows_evidence_id=item.follows_evidence_id,
                supplemental=item.chunk_id in supplemental_ids)
        try:
            with stage('generation'):
                draft = self.generator.generate_claims(search.query, tuple(inputs))
        except GenerationFormatError as error:
            log_verification_failure(error, 0)
            return insufficient(ReasonCode.VERIFICATION_FAILED, '생성된 답변의 응답 형식을 확인하지 못했습니다.')
        except GenerationUnavailable:
            raise AnswerGenerationError('답변 생성 서비스를 사용할 수 없습니다.') from None

        try:
            draft = _validate_draft(draft, evidence_by_id)
            if not draft.claims:
                return insufficient(ReasonCode.EVIDENCE_NOT_FOUND, '검색된 근거로 답변 초안을 구성하지 못했습니다.')
        except (ValueError, TypeError, AttributeError) as error:
            log_verification_failure(error, 0)
            return insufficient(ReasonCode.VERIFICATION_FAILED, '생성된 답변의 근거 연결을 확인하지 못했습니다.')

        def evaluate(candidate, round_number):
            assessments = []
            with stage('evidence_quality'):
                for number, claim in enumerate(candidate.claims, 1):
                    for evidence_id in claim.evidence_ids:
                        evidence_diagnostic('claim_selection', attempt_id=attempt_id,
                            round_number=round_number, claim_number=number, evidence_id=evidence_id,
                            support_count=sum(s.evidence_id == evidence_id for s in claim.supports))
                    assessment = assess_extraction_risk(claim, inputs)
                    assessments.append(assessment)
                    evidence_diagnostic('quality', attempt_id=attempt_id, round_number=round_number,
                        claim_number=number, evidence_id=assessment.evidence_id,
                        reason_code=assessment.reason.value)
            return assessments

        assessments = evaluate(draft, 0)
        targets = tuple(ContinuationRepairTarget(number, assessment.evidence_id, item.evidence_id)
            for number, (claim, assessment) in enumerate(zip(draft.claims, assessments), 1)
            if assessment.reason == ExtractionReason.UNFINISHED_TAIL
            for item in inputs if item.follows_evidence_id == assessment.evidence_id
            and item.evidence_id not in claim.evidence_ids)
        # 앞부분 연결·원문 불일치는 다음 후보가 없어도 기존 후보 안에서 구절을 재선택한다.
        exclusion_reasons = {ExtractionReason.UNRESOLVED_PREFIX, ExtractionReason.SUPPORT_NOT_FOUND}
        targets += tuple(ContinuationRepairTarget(number, assessment.evidence_id,
                                                  reason_code=assessment.reason.value)
            for number, assessment in enumerate(assessments, 1) if assessment.reason in exclusion_reasons)
        repair = getattr(self.generator, 'repair_claims', None)
        final_round = 0
        generator_excluded = False
        if targets and callable(repair) and not repair_budget.used:
            repair_budget.used = True
            target_numbers = sorted({target.claim_number for target in targets})
            target_set = set(target_numbers)
            # 정상 주장은 보완 입력에서도 분리한다. 제공자가 다시 작성할 권한을 주지 않는다.
            target_draft = GeneratedAnswerDraft(tuple(draft.claims[n - 1] for n in target_numbers))
            local_numbers = {number: index for index, number in enumerate(target_numbers, 1)}
            local_targets = tuple(replace(target, claim_number=local_numbers[target.claim_number])
                                  for target in targets)
            for target in targets:
                evidence_diagnostic('repair_target', attempt_id=attempt_id, round_number=1,
                    claim_number=target.claim_number, evidence_id=target.evidence_id,
                    next_evidence_id=target.next_evidence_id, reason_code=target.reason_code)
            try:
                with stage('generation_repair'):
                    repaired = repair(search.query, tuple(inputs), target_draft, local_targets)
                    repaired = _validate_draft(repaired, evidence_by_id)
                    if len(repaired.claims) > len(target_numbers):
                        raise ValueError('보완 대상보다 많은 주장을 반환했습니다.')
                    for number, claim in enumerate(repaired.claims, 1):
                        if {s.evidence_id for s in claim.supports} != set(claim.evidence_ids):
                            raise _DraftValidationError('SUPPORT_REFERENCE_INVALID', number)
                    repaired_assessments = evaluate(repaired, 1)
            except GenerationUnavailable:
                outcome = 'REPAIR_UNAVAILABLE'
                replacements, replacement_assessments = [], []
            except (GenerationFormatError, ValueError, TypeError, AttributeError) as error:
                log_verification_failure(error, 1)
                outcome = 'REPAIR_INVALID'
                replacements, replacement_assessments = [], []
            else:
                replacements, replacement_assessments = [], []
                for number, (claim, assessment) in enumerate(zip(repaired.claims, repaired_assessments), 1):
                    if assessment.risky:
                        evidence_diagnostic('claim_excluded', attempt_id=attempt_id, round_number=1,
                            claim_number=number, evidence_id=assessment.evidence_id,
                            reason_code=assessment.reason.value)
                    else:
                        replacements.append(claim)
                        replacement_assessments.append(assessment)
                final_round = 1
                outcome = 'REPAIR_ADOPTED'
            generator_excluded = (len(replacements) < len(target_numbers)
                                  or outcome != 'REPAIR_ADOPTED'
                                  or len(replacements) < len(repaired.claims))
            if outcome != 'REPAIR_ADOPTED':
                for number in target_numbers:
                    assessment = assessments[number - 1]
                    evidence_diagnostic('claim_excluded', attempt_id=attempt_id, round_number=0,
                        claim_number=number, evidence_id=assessment.evidence_id,
                        reason_code=assessment.reason.value)
            # 대상 위치에 검사를 통과한 보완만 넣고, 비대상 주장·supports는 그대로 보존한다.
            combined, combined_assessments = [], []
            for number, (claim, assessment) in enumerate(zip(draft.claims, assessments), 1):
                if number == target_numbers[0]:
                    combined.extend(replacements)
                    combined_assessments.extend(replacement_assessments)
                if number not in target_set:
                    combined.append(claim)
                    combined_assessments.append(assessment)
            draft, assessments = GeneratedAnswerDraft(tuple(combined)), combined_assessments
            evidence_diagnostic('repair_outcome', attempt_id=attempt_id,
                                round_number=1, reason_code=outcome)
            if not draft.claims:
                return insufficient(ReasonCode.EXTRACTION_LIMITED,
                    '보완 대상 주장의 원문 근거를 확인하지 못하여 답변을 제공하지 않습니다.')

        # 보완 미지원·실패 때도 확인되지 않은 앞부분이나 없는 구절의 주장은 노출하지 않는다.
        retained, retained_assessments = [], []
        excluded = 0
        for number, (claim, assessment) in enumerate(zip(draft.claims, assessments), 1):
            if assessment.reason in exclusion_reasons:
                excluded += 1
                evidence_diagnostic('claim_excluded', attempt_id=attempt_id,
                    claim_number=number, evidence_id=assessment.evidence_id,
                    reason_code=assessment.reason.value,
                    round_number=final_round)
            else:
                retained.append(claim)
                retained_assessments.append(assessment)
        draft, assessments = GeneratedAnswerDraft(tuple(retained)), retained_assessments
        if not draft.claims:
            return insufficient(ReasonCode.EXTRACTION_LIMITED,
                '원문 구절과 연결을 확인하지 못한 주장을 제외하여 답변을 제공하지 않습니다.')

        claims = tuple(Claim(new_id(), claim.text, claim.evidence_ids) for claim in draft.claims)
        used_ids = dict.fromkeys(eid for claim in claims for eid in claim.evidence_ids)
        used = tuple(evidence_by_id[eid] for eid in used_ids)
        with stage('evidence_validation'):
            citations = tuple(citation_from_evidence(evidence, search) for evidence in used)
            risky = [number for number, assessment in enumerate(assessments, 1) if assessment.risky]
            answer_ko = '\n'.join(c.text for c in claims)
            if risky:
                numbers = ', '.join(map(str, risky))
                answer_ko += (f'\n\n주의: {numbers}번 주장은 끊긴 원문 또는 확인되지 않은 근거 구절·숫자 출처에 '
                              '기반할 수 있어 추가 확인이 필요합니다.')
            if excluded or generator_excluded:
                answer_ko += '\n\n주의: 원문 구절과 연결을 확인하지 못한 일부 주장을 제외했습니다. 질문의 일부에 답하지 못했을 수 있습니다.'
            limited = bool(risky or excluded or generator_excluded)
            result = AnswerResult(AnswerStatus.PARTIAL if limited else AnswerStatus.OK,
                                  answer_ko, claims, citations,
                                  ReasonCode.EXTRACTION_LIMITED if limited else None)
            validate_answer_against_search(result, search)
        # 저장 성공 전에는 성공 응답을 반환하지 않는다. DB 오류는 숨기지 않는다.
        with stage('evidence_save'):
            self.evidence_writer.save(context_id, used)
        return AnswerResponse(context_id, search, result)
