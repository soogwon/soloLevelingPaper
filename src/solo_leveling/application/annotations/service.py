"""사용자 기록을 학습 맥락 안에서 관리하고, 저장한 원문 위치를 다시 확인한다."""
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from solo_leveling.domain.annotation import (
    Annotation, AnnotationOutcome, AnnotationPage, AnnotationResult, AnnotationValidationError,
    KINDS, SegmentInput,
)
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.infrastructure.database import annotations as store


class _Omitted(Enum):
    FIELD = 'omitted'


@dataclass(frozen=True)
class AnnotationSource:
    position: int
    pdf_page: int | None
    before: str
    quote: str
    after: str


@dataclass(frozen=True)
class AnnotationDetail:
    annotation: Annotation
    # annotation.segments와 같은 순서이며, 여러 구간 중 일부만 유효할 수도 있다.
    segment_available: tuple[bool, ...]
    source: AnnotationSource | None = None

    @property
    def location_status(self) -> Literal['available', 'unavailable', 'not_applicable']:
        if not self.segment_available:
            return 'not_applicable'
        return 'available' if all(self.segment_available) else 'unavailable'


def _require_context(context_id: str) -> None:
    if not isinstance(context_id, str) or not context_id.strip():
        raise AnnotationValidationError('학습 맥락 ID가 필요합니다.')


def _validated_segments(segments: Sequence[SegmentInput]) -> tuple[SegmentInput, ...]:
    if isinstance(segments, (str, bytes, bytearray, memoryview)) or not isinstance(segments, Sequence):
        raise AnnotationValidationError('구간 목록이 필요합니다.')
    segments = tuple(segments)
    for segment in segments:
        if not isinstance(segment, SegmentInput):
            raise AnnotationValidationError('구간은 SegmentInput이어야 합니다.')
    return segments


class AnnotationService:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def create(self, context_id: str, kind: str, body: str, *,
               segments: Sequence[SegmentInput] = (), step: str | None = None,
               request_key: str | None = None, creation_origin: str = 'unknown') -> AnnotationResult:
        _require_context(context_id)
        segments = _validated_segments(segments)
        return store.create_annotation(self.db_path, context_id, kind, body, segments, step,
                                       request_key=request_key, creation_origin=creation_origin)

    def get(self, context_id: str, annotation_id: str, *, source_position: int | None = None) -> AnnotationDetail:
        _require_context(context_id)
        record, originals = store.get_annotation_with_originals(self.db_path, context_id, annotation_id)
        available = []
        source = None
        for segment in record.segments:
            text, page = originals.get(segment.position, (None, None))
            start, end = segment.start_offset, segment.end_offset
            matches = (text is not None and 0 <= start < end <= len(text)
                       and text[start:end] == segment.quote_snapshot)
            available.append(matches)
            if matches and segment.position == source_position:
                # 선택 구간 앞뒤로 최대 120자만 제공한다.
                source = AnnotationSource(segment.position, page, text[max(0, start-120):start],
                                          text[start:end], text[end:end+120])
        return AnnotationDetail(record, tuple(available), source)

    def list(self, context_id: str, *, step: str | None = None, kind: str | None = None,
             limit: int | None = None, cursor: str | None = None) -> AnnotationPage:
        _require_context(context_id)
        if step is not None and step not in LEARNING_STEPS:
            raise AnnotationValidationError('지원하지 않는 학습 단계입니다.')
        if kind is not None and kind not in KINDS:
            raise AnnotationValidationError('지원하지 않는 기록 종류입니다.')
        return store.list_annotation_page(self.db_path, context_id, step, kind, limit=limit, cursor=cursor)

    def update(self, context_id: str, annotation_id: str, expected_revision: int, *,
               body: str | _Omitted = _Omitted.FIELD,
               step: str | None | _Omitted = _Omitted.FIELD,
               segments: Sequence[SegmentInput] | _Omitted = _Omitted.FIELD) -> AnnotationResult:
        _require_context(context_id)
        if segments is not _Omitted.FIELD:
            _validated_segments(segments)
            raise AnnotationValidationError('저장된 구간은 변경할 수 없습니다.')
        if type(expected_revision) is not int or expected_revision < 1:
            raise AnnotationValidationError('expected_revision은 1 이상의 정수여야 합니다.')
        if body is _Omitted.FIELD and step is _Omitted.FIELD:
            raise AnnotationValidationError('수정할 필드가 필요합니다.')
        if body is _Omitted.FIELD or step is _Omitted.FIELD:
            current = store.get_annotation(self.db_path, context_id, annotation_id)
            if current is None:
                return AnnotationResult(AnnotationOutcome.NOT_FOUND)
            # 생략된 필드는 사용자가 수정 대상으로 지정한 리비전에서만 가져온다.
            if current.revision != expected_revision:
                return AnnotationResult(AnnotationOutcome.CONFLICT)
            if body is _Omitted.FIELD:
                body = current.body
            if step is _Omitted.FIELD:
                step = current.step
        return store.update_annotation(self.db_path, context_id, annotation_id, expected_revision, body, step)

    def delete(self, context_id: str, annotation_id: str, expected_revision: int) -> AnnotationResult:
        _require_context(context_id)
        return store.delete_annotation(self.db_path, context_id, annotation_id, expected_revision)
