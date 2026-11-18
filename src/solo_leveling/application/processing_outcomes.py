"""#517 B 처리의 안전한 분류 힌트. 공개 응답·영속 상태·재시도 허가를 만들지 않는다."""

from dataclasses import dataclass
from typing import Literal

from solo_leveling.application.provider_dispatch import DispatchBudgetExceeded, DispatchError
from solo_leveling.domain.remote_processing import (
    EmbeddingBatch, EmbeddingSpec, ParsedDocument, ProcessedTranslation, ProcessingChunk,
    ProcessingError, ProcessingResult, ProcessingSource,
)
from solo_leveling.domain.translation import TranslationSettings, TranslationStatus


@dataclass(frozen=True)
class ProcessingAdvice:
    # A는 이 값으로 job 상태를 바로 쓰지 않고 현재 실행·원장·게시 상태를 검증한다.
    action: Literal['await_publication', 'stop', 'reconcile_usage']
    error_code: str | None
    retryable: Literal[False] | None
    requires_authoritative_review: bool = True


_STOP = ProcessingAdvice('stop', 'processing_failed', None)
_USAGE_PENDING = ProcessingAdvice('reconcile_usage', 'usage_pending', None)
_SOURCE_INVALID = ProcessingAdvice('stop', 'source_validation_failed', False)
_SOURCE_CODES = frozenset({
    'PROCESSING_PDF_MISMATCH', 'PROCESSING_PDF_UNREADABLE', 'PROCESSING_PDF_ENCRYPTED',
    'PROCESSING_EMPTY_DOCUMENT', 'PROCESSING_LIMIT_EXCEEDED',
})
_USAGE_CODES = frozenset({'DISPATCH_USAGE_UNKNOWN', 'DISPATCH_RECONCILIATION_UNAVAILABLE'})


def _usage_guard(advice: ProcessingAdvice, usage_unresolved: bool | None) -> ProcessingAdvice:
    # A가 같은 job/attempt의 원장에서 확인한 미정산 상태는 후속 오류나 prepared보다 우선한다.
    # False/None 자체는 정산 완료·재전송·게시 허가가 아니다. 잘못된 타입도 허용하지 않는다.
    if advice is _USAGE_PENDING or usage_unresolved is True:
        return _USAGE_PENDING
    if usage_unresolved is False or usage_unresolved is None:
        return advice
    return _STOP


def processing_error_advice(error: BaseException, *, usage_unresolved: bool | None = None) -> ProcessingAdvice:
    """오직 고정 내부 예외 타입·정확한 코드만 읽는다. str/repr·원인 예외는 읽지 않는다.

    NOT_ALLOWED/BLOCKED 등의 원인은 A가 영속 상태로 구분해야 한다. 이 함수는
    재전송·예약 해제·성공·취소 상태 전환을 허가하지 않는다.
    """
    if type(error) not in (ProcessingError, DispatchError, DispatchBudgetExceeded):
        return _usage_guard(_STOP, usage_unresolved)
    args = error.args
    if len(args) != 1 or type(args[0]) is not str:
        return _usage_guard(_STOP, usage_unresolved)
    code = args[0]
    if type(error) is ProcessingError and code in _SOURCE_CODES:
        return _usage_guard(_SOURCE_INVALID, usage_unresolved)
    if type(error) is DispatchError and code in _USAGE_CODES:
        return _usage_guard(_USAGE_PENDING, usage_unresolved)
    return _usage_guard(_STOP, usage_unresolved)


def _prepared_shape(result: ProcessingResult) -> bool:
    """분류 전 완성 결과의 기본 연결·개수를 확인한다. 벡터 수치 검사는 기존 stages 책임이다."""
    if (type(result.source) is not ProcessingSource or type(result.document) is not ParsedDocument
            or type(result.translation_settings) is not TranslationSettings
            or type(result.translations) is not tuple or not result.translations
            or type(result.document.chunks) is not tuple
            or type(result.embeddings) is not EmbeddingBatch
            or type(result.embeddings.spec) is not EmbeddingSpec):
        return False
    chunks, translations, embeddings = result.document.chunks, result.translations, result.embeddings
    if (len(chunks) != len(translations) or type(embeddings.chunk_ids) is not tuple
            or type(embeddings.vectors) is not tuple or len(embeddings.vectors) != len(chunks)
            or type(embeddings.spec.dimension) is not int or embeddings.spec.dimension <= 0
            or any(type(v) is not tuple or len(v) != embeddings.spec.dimension for v in embeddings.vectors)):
        return False
    for chunk, item in zip(chunks, translations):
        if (type(chunk) is not ProcessingChunk or type(item) is not ProcessedTranslation
                or type(chunk.chunk_id) is not str or not chunk.chunk_id.strip()
                or chunk.parse_revision_id != result.source.parse_revision_id
                or type(item.chunk_id) is not str or item.chunk_id != chunk.chunk_id or item.failure_code is not None
                or not isinstance(item.status, TranslationStatus)
                or type(item.text) is not str or not item.text.strip()):
            return False
    ids = tuple(c.chunk_id for c in chunks)
    return len(set(ids)) == len(ids) and embeddings.chunk_ids == ids


def processing_result_advice(result: ProcessingResult, *, usage_unresolved: bool | None = None) -> ProcessingAdvice:
    """분류 전 구조를 보수적으로 확인한다. 결과·지문·벡터 검증기와 게시 권한을 대신하지 않는다."""
    if type(result) is not ProcessingResult or type(result.status) is not str:
        return _usage_guard(_STOP, usage_unresolved)
    if result.status == 'prepared' and _prepared_shape(result):
        # 준비 결과가 있어도 publication·searchable·succeeded를 생성하지 않는다.
        return _usage_guard(ProcessingAdvice('await_publication', None, None), usage_unresolved)
    # 부분 번역은 명시적인 부분 게시 정책이 확정되기 전 미게시 실패다.
    # provider_unavailable에는 영구 오류도 포함되므로 일시 장애/재시도 가능으로 추정하지 않는다.
    return _usage_guard(_STOP, usage_unresolved)


def processing_phase_hint(stage: str) -> str | None:
    """B checkpoint의 다음 단계 의도. A의 영속 phase 관측이나 공개 응답이 아니다."""
    if type(stage) is not str:
        return None
    # result는 게시 시작·완료를 의미하지 않는다. 알 수 없는 값도 추정하지 않는다.
    return {'parse': 'parsing', 'translation': 'translating', 'embedding': 'embedding'}.get(stage)
