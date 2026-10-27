"""의미 검증의 판정·실행 결과. 모델 판정은 사실성 보증이 아니다."""

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from solo_leveling.domain.evidence_qa import require_text


class VerificationLevel(str, Enum):
    STRUCTURAL_ONLY = 'structural_only'
    MODEL_CHECKED = 'model_checked'


class SemanticVerdict(str, Enum):
    SUPPORTED = 'supported'
    PARTIALLY_SUPPORTED = 'partially_supported'
    CONTRADICTED = 'contradicted'
    UNDETERMINED = 'undetermined'


class SemanticReason(str, Enum):
    FULL_SUPPORT = 'full_support'
    MISSING_CONDITION = 'missing_condition'
    UNSUPPORTED_DIRECTION = 'unsupported_direction'
    UNSUPPORTED_CAUSALITY = 'unsupported_causality'
    METRIC_CONFUSION = 'metric_confusion'
    PARTIAL_SUPPORT = 'partial_support'
    CONTRADICTION = 'contradiction'
    INSUFFICIENT_SUPPORT = 'insufficient_support'
    DAMAGED_EVIDENCE = 'damaged_evidence'


# API 출력 스키마와 서버 검증이 같은 판정·사유 계약을 사용한다.
SEMANTIC_REASON_RULES = MappingProxyType({
    SemanticVerdict.SUPPORTED: (SemanticReason.FULL_SUPPORT,),
    SemanticVerdict.PARTIALLY_SUPPORTED: (
        SemanticReason.MISSING_CONDITION, SemanticReason.UNSUPPORTED_DIRECTION,
        SemanticReason.UNSUPPORTED_CAUSALITY, SemanticReason.METRIC_CONFUSION,
        SemanticReason.PARTIAL_SUPPORT),
    SemanticVerdict.CONTRADICTED: (SemanticReason.CONTRADICTION,),
    SemanticVerdict.UNDETERMINED: (
        SemanticReason.INSUFFICIENT_SUPPORT, SemanticReason.DAMAGED_EVIDENCE),
})


class VerificationFailure(str, Enum):
    INPUT_LIMIT = 'input_limit'
    TIMEOUT = 'timeout'
    UNAVAILABLE = 'unavailable'
    INVALID_RESPONSE = 'invalid_response'
    EXTERNAL_DISABLED = 'external_disabled'


class VerificationDetail(str, Enum):
    PROVIDER_JSON_INVALID = 'PROVIDER_JSON_INVALID'
    PROVIDER_ENVELOPE_INVALID = 'PROVIDER_ENVELOPE_INVALID'
    PROVIDER_INCOMPLETE = 'PROVIDER_INCOMPLETE'
    CLAIM_CHECK_INVALID = 'CLAIM_CHECK_INVALID'
    CLAIM_REFERENCE_INVALID = 'CLAIM_REFERENCE_INVALID'
    ORIGINAL_QUOTE_POLICY_INVALID = 'ORIGINAL_QUOTE_POLICY_INVALID'
    ORIGINAL_QUOTE_REFERENCE_INVALID = 'ORIGINAL_QUOTE_REFERENCE_INVALID'
    ORIGINAL_QUOTE_NOT_FOUND = 'ORIGINAL_QUOTE_NOT_FOUND'
    ORIGINAL_QUOTE_ID_UNKNOWN = 'ORIGINAL_QUOTE_ID_UNKNOWN'
    ORIGINAL_QUOTE_ID_DUPLICATE = 'ORIGINAL_QUOTE_ID_DUPLICATE'
    CLAIM_SET_INVALID = 'CLAIM_SET_INVALID'
    AUDIT_FORMAT_INVALID = 'AUDIT_FORMAT_INVALID'
    COVERAGE_REFERENCE_INVALID = 'COVERAGE_REFERENCE_INVALID'
    QUESTION_PARTITION_MISMATCH = 'QUESTION_PARTITION_MISMATCH'
    REPAIR_REFERENCE_INVALID = 'REPAIR_REFERENCE_INVALID'
    REPAIR_COVERAGE_MISSING = 'REPAIR_COVERAGE_MISSING'
    CHECK_FIELDS_INVALID = 'CHECK_FIELDS_INVALID'
    CHECK_FORMAT_INVALID = 'CHECK_FORMAT_INVALID'
    QUOTE_REQUIRED = 'QUOTE_REQUIRED'
    QUOTE_FORBIDDEN = 'QUOTE_FORBIDDEN'
    CHECK_NOT_APPLICABLE = 'CHECK_NOT_APPLICABLE'
    ANSWER_QUOTE_REFERENCE_INVALID = 'ANSWER_QUOTE_REFERENCE_INVALID'
    ANSWER_QUOTE_NOT_FOUND = 'ANSWER_QUOTE_NOT_FOUND'
    ANSWER_QUOTE_ID_UNKNOWN = 'ANSWER_QUOTE_ID_UNKNOWN'
    ANSWER_QUOTE_ID_DUPLICATE = 'ANSWER_QUOTE_ID_DUPLICATE'
    CONTRACT_INVALID = 'CONTRACT_INVALID'
    METRIC_SUBSTITUTION = 'METRIC_SUBSTITUTION'
    VARIABLE_ONLY_CONDITION = 'VARIABLE_ONLY_CONDITION'


AUDIT_CHECK_LOCATIONS = frozenset({
    'coverage.topic', 'coverage.requested_relation', 'coverage.qualifiers',
    'repairs.core_fact', 'repairs.comparison_basis', 'repairs.qualifiers',
    'original.content', 'original.direction', 'original.conditions', 'original.causality', 'original.metrics',
})

CHECK_DETAIL_CODES = frozenset({VerificationDetail.QUOTE_REQUIRED, VerificationDetail.ANSWER_QUOTE_REFERENCE_INVALID,
    VerificationDetail.ORIGINAL_QUOTE_POLICY_INVALID, VerificationDetail.ORIGINAL_QUOTE_REFERENCE_INVALID,
    VerificationDetail.ORIGINAL_QUOTE_NOT_FOUND, VerificationDetail.ORIGINAL_QUOTE_ID_UNKNOWN,
    VerificationDetail.ORIGINAL_QUOTE_ID_DUPLICATE, VerificationDetail.CLAIM_CHECK_INVALID})

COVERAGE_REJECTION_REASONS = frozenset({
    'ITEM_FIELDS_INVALID', 'QUESTION_PART_INVALID', 'STATUS_INVALID', 'CLAIM_IDS_TYPE_INVALID',
    'CLAIM_ID_TYPE_INVALID', 'UNKNOWN_CLAIM_ID', 'DUPLICATE_CLAIM_ID',
    'UNANSWERED_HAS_CLAIM_IDS', 'ANSWERED_WITHOUT_CLAIM_IDS', 'PARTIAL_WITHOUT_CLAIM_IDS',
})


class AuditValidationError(ValueError):
    """본문 없이 고정 코드만 보존하는 답변 검사 오류다."""
    def __init__(self, detail: VerificationDetail, *, check=None, subreason=None):
        self.detail = detail
        self.check = check if check in AUDIT_CHECK_LOCATIONS else None
        self.subreason = subreason if subreason in COVERAGE_REJECTION_REASONS else None
        super().__init__(detail.value)


@dataclass(frozen=True)
class ClaimJudgment:
    claim_id: str
    verdict: SemanticVerdict
    reason: SemanticReason
    checks: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class QuestionCoverage:
    question_part: str
    status: str
    claim_ids: tuple[str, ...]
    checks: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RepairPreservation:
    source_id: str
    status: str
    checks: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AnswerAudit:
    coverage: tuple[QuestionCoverage, ...]
    repairs: tuple[RepairPreservation, ...]
    detail_codes: tuple[VerificationDetail, ...] = ()


@dataclass(frozen=True)
class VerificationReport:
    run_id: str
    model: str
    prompt_version: str
    elapsed_ms: int
    judgments: tuple[ClaimJudgment, ...]
    failure: VerificationFailure | None = None
    # 애플리케이션의 답변 완전성 검사 결과이며 DB에 저장하지 않는다.
    answer_audit: AnswerAudit | None = None
    detail_code: VerificationDetail | None = None
    detail_check: str | None = None
    detail_subreason: str | None = None

    def __post_init__(self):
        for name in ('run_id', 'model', 'prompt_version'):
            require_text(getattr(self, name), name)
        if type(self.elapsed_ms) is not int or self.elapsed_ms < 0:
            raise ValueError('검증 소요 시간은 음수가 아닌 정수여야 합니다.')
        if not isinstance(self.judgments, tuple):
            raise ValueError('검증 판정은 튜플이어야 합니다.')
        if self.failure is not None and (not isinstance(self.failure, VerificationFailure) or self.judgments):
            raise ValueError('실패한 검증에는 완료 판정을 기록하지 않습니다.')
        if self.answer_audit is not None and (not isinstance(self.answer_audit, AnswerAudit) or self.failure):
            raise ValueError('완료한 검증에만 답변 검사 결과를 기록합니다.')
        if self.detail_code is not None and not isinstance(self.detail_code, VerificationDetail):
            raise ValueError('검증 거부 사유는 고정 코드여야 합니다.')
        if self.detail_check is not None and (
                self.detail_check not in AUDIT_CHECK_LOCATIONS or self.detail_code not in CHECK_DETAIL_CODES):
            raise ValueError('검사 위치는 허용된 구절 누락 항목이어야 합니다.')
        if self.detail_subreason is not None and (
                self.detail_code != VerificationDetail.COVERAGE_REFERENCE_INVALID
                or self.detail_subreason not in COVERAGE_REJECTION_REASONS):
            raise ValueError('질문 참조 거부 사유는 허용된 고정 코드여야 합니다.')

    @property
    def level(self) -> VerificationLevel:
        return (VerificationLevel.STRUCTURAL_ONLY if self.failure is not None
                else VerificationLevel.MODEL_CHECKED)
