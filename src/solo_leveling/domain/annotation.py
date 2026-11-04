"""메모·하이라이트(M1-11, #56)의 도메인 모델과 형식 검증.

- 기록(annotation) 하나에 선택 구간(segment)이 0개 이상 붙는다. 메모(note)는 본문이 필수이고, 하이라이트(highlight)는 구간이 하나 이상 필요하다.
- 위치는 저장된 청크 텍스트의 문자 위치로 고정한다: 유니코드 코드 포인트 단위, 0부터, [start_offset, end_offset).
  공백·줄바꿈·유니코드 정규화로 저장 문자열을 바꾸지 않는다. 인용문 스냅샷(quote_snapshot)으로 원문이 바뀌었는지 확인한다.
- 위치(맥락·청크·구간)는 바꾸지 않는다. 바꾸려면 새 기록을 만든다. 본문과 학습 단계만 수정할 수 있다.
- 메모 본문과 인용문은 사용자 데이터라 로그에 남기지 않는다. 오류에는 고정 코드만 쓴다.
"""
import base64
import binascii
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence, Tuple

from solo_leveling.domain.learning_progress import LEARNING_STEPS

KIND_NOTE = 'note'
KIND_HIGHLIGHT = 'highlight'
KINDS: tuple = (KIND_NOTE, KIND_HIGHLIGHT)

TARGET_ORIGINAL = 'original'        # 원문 청크(chunks.original_text)
TARGET_TRANSLATION = 'translation'  # 번역문(번역 리비전이 필요하다)
TARGETS: tuple = (TARGET_ORIGINAL, TARGET_TRANSLATION)

MAX_BODY_LENGTH = 5000
MAX_QUOTE_LENGTH = 2000
MAX_SEGMENTS = 20
MAX_QUOTE_TOTAL_LENGTH = 8000  # 기록 하나의 구간 인용문 길이 합계(#56)
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50

# 공개 인터페이스가 구분할 수 있는 고정 오류 코드(AnnotationValidationError.code)
DUPLICATE_SEGMENT = 'DUPLICATE_SEGMENT'
QUOTE_TOTAL_TOO_LONG = 'QUOTE_TOTAL_TOO_LONG'
INVALID_LIMIT = 'INVALID_LIMIT'
INVALID_CURSOR = 'INVALID_CURSOR'


class AnnotationValidationError(ValueError):
    """기록·구간의 형식이 맞지 않는다. 본문·인용문은 메시지에 넣지 않는다.

    code는 호출자가 구분해야 하는 오류에만 있는 고정 문자열이고, 나머지는 None이다.
    """

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class AnnotationOutcome(str, Enum):
    """저장소가 예외 대신 돌려주는 결과. 형식 오류만 AnnotationValidationError로 낸다."""
    SAVED = 'saved'
    DELETED = 'deleted'
    CONFLICT = 'conflict'                    # expected_revision이 현재와 다르다. 아무것도 바꾸지 않았다.
    NOT_FOUND = 'not_found'                  # 기록이 없다.
    INVALID_REFERENCE = 'invalid_reference'  # 맥락·청크·리비전·위치·인용문이 맞지 않는다. code에 이유.


# INVALID_REFERENCE의 이유 코드(고정 문자열)
CONTEXT_NOT_FOUND = 'CONTEXT_NOT_FOUND'
CHUNK_NOT_IN_CONTEXT = 'CHUNK_NOT_IN_CONTEXT'
TRANSLATION_REVISION_MISMATCH = 'TRANSLATION_REVISION_MISMATCH'
OFFSET_OUT_OF_RANGE = 'OFFSET_OUT_OF_RANGE'
QUOTE_MISMATCH = 'QUOTE_MISMATCH'


@dataclass(frozen=True)
class SegmentInput:
    """생성할 선택 구간. 번역 구간(target='translation')은 번역 리비전이 필요하다."""
    chunk_id: str
    start_offset: int
    end_offset: int
    quote_snapshot: str
    target: str = TARGET_ORIGINAL
    translation_revision_id: Optional[str] = None

    def __post_init__(self):
        if self.target not in TARGETS:
            raise AnnotationValidationError('지원하지 않는 구간 대상')
        if not isinstance(self.chunk_id, str) or not self.chunk_id.strip():
            raise AnnotationValidationError('chunk_id가 비어 있음')
        for value in (self.start_offset, self.end_offset):
            if isinstance(value, bool) or not isinstance(value, int):
                raise AnnotationValidationError('오프셋은 정수여야 함')
        if not 0 <= self.start_offset < self.end_offset:
            raise AnnotationValidationError('오프셋 범위가 올바르지 않음')
        if not isinstance(self.quote_snapshot, str) or not self.quote_snapshot:
            raise AnnotationValidationError('인용문 스냅샷이 비어 있음')
        if len(self.quote_snapshot) > MAX_QUOTE_LENGTH:
            raise AnnotationValidationError('인용문 스냅샷이 너무 김')
        if self.end_offset - self.start_offset != len(self.quote_snapshot):
            raise AnnotationValidationError('인용문 길이와 오프셋 범위가 다름')
        if self.target == TARGET_TRANSLATION and not self.translation_revision_id:
            raise AnnotationValidationError('번역 구간에는 번역 리비전이 필요함')
        if self.target == TARGET_ORIGINAL and self.translation_revision_id is not None:
            raise AnnotationValidationError('원문 구간에는 번역 리비전을 쓸 수 없음')


@dataclass(frozen=True)
class AnnotationSegment:
    """저장된 선택 구간. parse_revision_id는 생성 시점 청크의 파싱 리비전이다."""
    position: int
    chunk_id: str
    parse_revision_id: str
    target: str
    start_offset: int
    end_offset: int
    quote_snapshot: str
    translation_revision_id: Optional[str] = None


@dataclass(frozen=True)
class Annotation:
    annotation_id: str
    context_id: str
    kind: str
    body: str
    step: Optional[str]
    revision: int  # 수정할 때마다 1씩 늘어난다. 수정·삭제 때 expected_revision과 비교한다.
    created_at: str
    updated_at: str
    segments: Tuple[AnnotationSegment, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AnnotationResult:
    outcome: AnnotationOutcome
    annotation: Optional[Annotation] = None
    code: Optional[str] = None  # INVALID_REFERENCE의 이유 코드


def validate_annotation_fields(kind: str, body: str, step: Optional[str], segment_count: int) -> None:
    if kind not in KINDS:
        raise AnnotationValidationError('지원하지 않는 기록 종류')
    if not isinstance(body, str):
        raise AnnotationValidationError('본문은 문자열이어야 함')
    if len(body) > MAX_BODY_LENGTH:
        raise AnnotationValidationError('본문이 너무 김')
    if kind == KIND_NOTE and not body.strip():
        raise AnnotationValidationError('메모 본문이 비어 있음')
    if kind == KIND_HIGHLIGHT and segment_count < 1:
        raise AnnotationValidationError('하이라이트에는 구간이 하나 이상 필요함')
    if segment_count > MAX_SEGMENTS:
        raise AnnotationValidationError('구간이 너무 많음')
    if step is not None and step not in LEARNING_STEPS:
        raise AnnotationValidationError('지원하지 않는 학습 단계')


def validate_segments(segments: Sequence[SegmentInput]) -> None:
    """구간 묶음의 규칙: 인용문 길이 합계 제한, 같은 구간 중복 금지. 일부만 겹치는 구간은 허용한다.

    중복 기준은 (chunk_id, target, translation_revision_id, start, end)가 모두 같을 때다.
    원문 구간의 번역 리비전은 None으로 비교한다. 한 기록 안에서만 비교한다.
    """
    if sum(len(s.quote_snapshot) for s in segments) > MAX_QUOTE_TOTAL_LENGTH:
        raise AnnotationValidationError('인용문 길이 합계가 너무 김', QUOTE_TOTAL_TOO_LONG)
    seen = set()
    for s in segments:
        key = (s.chunk_id, s.target, s.translation_revision_id, s.start_offset, s.end_offset)
        if key in seen:
            raise AnnotationValidationError('같은 구간이 중복됨', DUPLICATE_SEGMENT)
        seen.add(key)


@dataclass(frozen=True)
class AnnotationPage:
    """목록 한 페이지. next_cursor가 None이면 마지막 페이지다."""
    items: Tuple[Annotation, ...]
    next_cursor: Optional[str] = None


def validate_page_limit(limit) -> int:
    """None이면 기본값. 정수 1..MAX_PAGE_SIZE만 허용한다(bool 제외)."""
    if limit is None:
        return DEFAULT_PAGE_SIZE
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise AnnotationValidationError('limit 범위가 올바르지 않음', INVALID_LIMIT)
    return limit


_CURSOR_FIELDS = ('c', 's', 'k', 't', 'i')
_MAX_CURSOR_LENGTH = 512


def encode_cursor(context_id: str, step: Optional[str], kind: Optional[str], created_at: str,
                  annotation_id: str) -> str:
    """다음 페이지의 시작 위치를 불투명 문자열로 만든다. 발급한 맥락·필터가 함께 들어간다."""
    raw = json.dumps({'c': context_id, 's': step, 'k': kind, 't': created_at, 'i': annotation_id},
                     separators=(',', ':'), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode('utf-8')).decode('ascii').rstrip('=')


def decode_cursor(cursor, context_id: str, step: Optional[str], kind: Optional[str]) -> Tuple[str, str]:
    """커서를 (created_at, annotation_id)로 푼다. 형식 오류이거나 다른 맥락·필터의 커서면 INVALID_CURSOR."""
    def invalid():
        return AnnotationValidationError('커서가 올바르지 않음', INVALID_CURSOR)

    if not isinstance(cursor, str) or not cursor or len(cursor) > _MAX_CURSOR_LENGTH:
        raise invalid()
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)).decode('utf-8'))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise invalid() from None
    if not isinstance(data, dict) or set(data) != set(_CURSOR_FIELDS):
        raise invalid()
    if (data['c'], data['s'], data['k']) != (context_id, step, kind):
        raise invalid()
    created_at, annotation_id = data['t'], data['i']
    if not all(isinstance(v, str) and v for v in (created_at, annotation_id)):
        raise invalid()
    return created_at, annotation_id
