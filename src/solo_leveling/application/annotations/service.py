"""사용자 기록을 학습 맥락 안에서 관리하고, 저장한 원문 위치를 다시 확인한다."""
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.domain.annotation import (
    Annotation, AnnotationOutcome, AnnotationResult, AnnotationValidationError,
    KINDS, SegmentInput, TARGET_ORIGINAL,
)
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.infrastructure.database import annotations as store


class _Omitted(Enum):
    FIELD = 'omitted'


@dataclass(frozen=True)
class AnnotationDetail:
    annotation: Annotation
    # annotation.segments와 같은 순서이며, 여러 구간 중 일부만 유효할 수도 있다.
    segment_available: tuple[bool, ...]

    @property
    def location_status(self) -> Literal['available', 'unavailable', 'not_applicable']:
        if not self.segment_available:
            return 'not_applicable'
        return 'available' if all(self.segment_available) else 'unavailable'


def _require_context(context_id: str) -> None:
    if not isinstance(context_id, str) or not context_id.strip():
        raise AnnotationValidationError('학습 맥락 ID가 필요합니다.')


class AnnotationService:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def create(self, context_id: str, kind: str, body: str, *,
               segments: Sequence[SegmentInput] = (), step: str | None = None) -> AnnotationResult:
        _require_context(context_id)
        if isinstance(segments, (str, bytes)) or not isinstance(segments, Sequence):
            raise AnnotationValidationError('구간 목록이 필요합니다.')
        segments = tuple(segments)
        for segment in segments:
            if not isinstance(segment, SegmentInput):
                raise AnnotationValidationError('구간은 SegmentInput이어야 합니다.')
            if segment.target != TARGET_ORIGINAL:
                raise AnnotationValidationError('현재 원문 구간만 지정할 수 있습니다.')
        return store.create_annotation(self.db_path, context_id, kind, body, segments, step)

    def get(self, context_id: str, annotation_id: str) -> AnnotationDetail:
        _require_context(context_id)
        record = store.get_annotation(self.db_path, context_id, annotation_id)
        if record is None:
            raise ResourceNotFoundError('annotation not found')
        if not record.segments:
            return AnnotationDetail(record, ())
        texts = store.get_segment_texts(self.db_path, context_id, annotation_id)
        available = []
        for segment in record.segments:
            text = texts.get(segment.position)
            available.append(
                segment.target == TARGET_ORIGINAL and text is not None
                and 0 <= segment.start_offset < segment.end_offset <= len(text)
                and text[segment.start_offset:segment.end_offset] == segment.quote_snapshot
            )
        return AnnotationDetail(record, tuple(available))

    def list(self, context_id: str, *, step: str | None = None, kind: str | None = None) -> list[Annotation]:
        _require_context(context_id)
        if step is not None and step not in LEARNING_STEPS:
            raise AnnotationValidationError('지원하지 않는 학습 단계입니다.')
        if kind is not None and kind not in KINDS:
            raise AnnotationValidationError('지원하지 않는 기록 종류입니다.')
        return store.list_annotations(self.db_path, context_id, step, kind)

    def update(self, context_id: str, annotation_id: str, expected_revision: int, *,
               body: str | _Omitted = _Omitted.FIELD,
               step: str | None | _Omitted = _Omitted.FIELD) -> AnnotationResult:
        _require_context(context_id)
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
