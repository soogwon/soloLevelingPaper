"""의미 검증 이력(M1, #83)의 저장 입력과 결과 코드.

- 한 번의 의미 검증 실행(run)과 그때 검사한 주장별 판정·참조를 기록한다. 답변 본문·질문·청크 원문·번역·키·전체 API 응답은
  이력에 복제하지 않는다. 주장 문구와 청크 참조(chunk_id + 원문 해시)만 남긴다.
- 저장소는 계약 결과를 예외가 아니라 HistoryOutcome으로 돌려준다. 형식 오류만 HistoryValidationError를 낸다.
- 검증 실행이 실패한 기록은 주장 행이 0개다. 검증을 마친 뒤 전부 제외했어도 실제 판정은 모두 남기며,
  verdict(모델 판정)와 retained(최종 답변 유지 여부)를 따로 둔다.
"""
import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from solo_leveling.domain.semantic_verification import (
    SEMANTIC_REASON_RULES, SemanticReason, SemanticVerdict, VerificationFailure, VerificationLevel)

MAX_CLAIM_TEXT = 4000
MAX_REFERENCES_PER_CLAIM = 50
MAX_CLAIMS_PER_RUN = 200


class HistoryValidationError(ValueError):
    """이력 입력의 형식이 맞지 않는다. 재시도해도 같은 오류라 결과 코드가 아닌 예외로 낸다."""


class HistoryOutcome(str, Enum):
    SAVED = 'saved'                        # 실행·주장·참조를 모두 저장했다.
    DUPLICATE_RUN = 'duplicate_run'        # 같은 run_id가 이미 있다. 아무것도 다시 저장하지 않는다.
    REFERENCE_MISMATCH = 'reference_mismatch'  # 맥락·리비전·색인 범위 또는 청크 원문 해시가 맞지 않는다.
    SAVE_FAILED = 'save_failed'            # 저장 중 DB 오류. 전부 되돌렸고, 세부 내용은 남기지 않는다.


def chunk_content_hash(original_text: str) -> str:
    """검사에 사용한 청크 원문 전체의 해시. 정규화하지 않고 UTF-8 SHA-256 그대로 쓴다."""
    return hashlib.sha256(original_text.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class HistoryReference:
    chunk_id: str
    content_hash: str


@dataclass(frozen=True)
class HistoryClaim:
    claim_id: str
    text: str
    verdict: str          # SemanticVerdict 값
    reason: str           # SemanticReason 값
    retained: bool        # 최종 답변에 남았는가(검사 뒤 답변 감사 단계에서 바뀔 수 있어 verdict와 구분한다)
    references: tuple[HistoryReference, ...]


@dataclass(frozen=True)
class VerificationHistoryRecord:
    run_id: str
    context_id: str
    version_id: str
    parse_revision_id: str
    translation_revision_id: str
    embedding_set_id: str
    model: str
    instruction_version: str
    elapsed_ms: int
    level: str            # VerificationLevel 값
    failure: Optional[str]  # VerificationFailure 값. 실패한 실행만 있다.
    claims: tuple[HistoryClaim, ...]
    created_at: Optional[str] = None  # 저장소가 정한다. 입력에서는 비운다.


@dataclass(frozen=True)
class HistoryResult:
    outcome: HistoryOutcome
    run_id: Optional[str] = None


def _text(value, name: str, limit: int = 500) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise HistoryValidationError(f'{name} 형식이 올바르지 않습니다.')


def validate_record(record: VerificationHistoryRecord) -> None:
    for name in ('run_id', 'context_id', 'version_id', 'parse_revision_id', 'translation_revision_id',
                 'embedding_set_id', 'model', 'instruction_version'):
        _text(getattr(record, name), name)
    if isinstance(record.elapsed_ms, bool) or not isinstance(record.elapsed_ms, int) or record.elapsed_ms < 0:
        raise HistoryValidationError('elapsed_ms는 0 이상의 정수여야 합니다.')
    if record.level not in {v.value for v in VerificationLevel}:
        raise HistoryValidationError('지원하지 않는 검증 수준입니다.')
    if record.failure is not None and record.failure not in {v.value for v in VerificationFailure}:
        raise HistoryValidationError('지원하지 않는 실패 코드입니다.')
    if not isinstance(record.claims, tuple) or len(record.claims) > MAX_CLAIMS_PER_RUN:
        raise HistoryValidationError('claims 형식이 올바르지 않습니다.')
    if record.failure is not None and record.claims:
        raise HistoryValidationError('실패한 검증 실행에는 주장 판정을 남기지 않습니다.')
    if record.failure is None and not record.claims:
        raise HistoryValidationError('검증을 마친 실행에는 주장 판정이 있어야 합니다.')
    claim_ids = set()
    for claim in record.claims:
        if not isinstance(claim, HistoryClaim):
            raise HistoryValidationError('claims 항목 형식이 올바르지 않습니다.')
        _text(claim.claim_id, 'claim_id')
        _text(claim.text, 'claim text', MAX_CLAIM_TEXT)
        if claim.claim_id in claim_ids:
            raise HistoryValidationError('claim_id가 중복되었습니다.')
        claim_ids.add(claim.claim_id)
        if claim.verdict not in {v.value for v in SemanticVerdict} or claim.reason not in {
                r.value for r in SemanticReason}:
            raise HistoryValidationError('판정 또는 사유가 올바르지 않습니다.')
        if SemanticReason(claim.reason) not in SEMANTIC_REASON_RULES[SemanticVerdict(claim.verdict)]:
            raise HistoryValidationError('판정과 사유가 맞지 않습니다.')
        if not isinstance(claim.retained, bool):
            raise HistoryValidationError('retained는 bool이어야 합니다.')
        refs = claim.references
        if not isinstance(refs, tuple) or not 1 <= len(refs) <= MAX_REFERENCES_PER_CLAIM:
            raise HistoryValidationError('주장에는 참조가 1개 이상 있어야 합니다.')
        chunk_ids = set()
        for ref in refs:
            if not isinstance(ref, HistoryReference):
                raise HistoryValidationError('참조 형식이 올바르지 않습니다.')
            _text(ref.chunk_id, 'chunk_id')
            if (not isinstance(ref.content_hash, str) or len(ref.content_hash) != 64
                    or any(c not in '0123456789abcdef' for c in ref.content_hash)):
                raise HistoryValidationError('content_hash는 SHA-256 16진 문자열이어야 합니다.')
            if ref.chunk_id in chunk_ids:
                raise HistoryValidationError('한 주장 안에서 청크 참조가 중복되었습니다.')
            chunk_ids.add(ref.chunk_id)
