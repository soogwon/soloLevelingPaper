"""원문 출처에 고정된 키워드·중요 문장 계약(#229)."""
from dataclasses import dataclass
from typing import Optional, Tuple

KIND_KEYWORD = 'keyword'
KIND_IMPORTANT_SENTENCE = 'important_sentence'
KINDS = (KIND_KEYWORD, KIND_IMPORTANT_SENTENCE)

CONTENT_EXTRACTED = 'extracted'
CONTENT_GENERATED = 'generated'
CONTENT_KINDS = (CONTENT_EXTRACTED, CONTENT_GENERATED)

PRODUCER_EXTRACTOR = 'extractor'
PRODUCER_MODEL = 'model'
PRODUCER_USER = 'user'
PRODUCERS = (PRODUCER_EXTRACTOR, PRODUCER_MODEL, PRODUCER_USER)


class PaperInsightValidationError(ValueError):
    """키워드·중요 문장 형식 또는 출처 연결이 올바르지 않다."""


@dataclass(frozen=True)
class PaperInsightInput:
    kind: str
    text: str
    content_kind: str
    producer: str
    chunk_id: str
    start_offset: int
    end_offset: int
    quote_original: str
    translation_revision_id: Optional[str] = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise PaperInsightValidationError('지원하지 않는 항목 종류입니다.')
        if not isinstance(self.text, str) or not self.text.strip():
            raise PaperInsightValidationError('항목 본문이 비어 있습니다.')
        if self.content_kind not in CONTENT_KINDS or self.producer not in PRODUCERS:
            raise PaperInsightValidationError('작성 방식이 올바르지 않습니다.')
        if self.content_kind == CONTENT_EXTRACTED and self.producer != PRODUCER_EXTRACTOR:
            raise PaperInsightValidationError('추출 항목의 작성 주체가 올바르지 않습니다.')
        if self.content_kind == CONTENT_GENERATED and self.producer == PRODUCER_EXTRACTOR:
            raise PaperInsightValidationError('생성 항목의 작성 주체가 올바르지 않습니다.')
        if not isinstance(self.chunk_id, str) or not self.chunk_id.strip():
            raise PaperInsightValidationError('chunk_id가 비어 있습니다.')
        if any(type(value) is not int for value in (self.start_offset, self.end_offset)):
            raise PaperInsightValidationError('오프셋은 정수여야 합니다.')
        if not 0 <= self.start_offset < self.end_offset:
            raise PaperInsightValidationError('오프셋 범위가 올바르지 않습니다.')
        if not isinstance(self.quote_original, str) or not self.quote_original:
            raise PaperInsightValidationError('원문 인용 스냅샷이 비어 있습니다.')
        if self.end_offset - self.start_offset != len(self.quote_original):
            raise PaperInsightValidationError('원문 인용과 오프셋 길이가 다릅니다.')
        if self.content_kind == CONTENT_EXTRACTED:
            if self.kind == KIND_IMPORTANT_SENTENCE and self.text != self.quote_original:
                raise PaperInsightValidationError('추출 중요 문장은 원문 인용과 같아야 합니다.')
            if self.kind == KIND_KEYWORD and self.text not in self.quote_original:
                raise PaperInsightValidationError('추출 키워드가 원문 인용에 없습니다.')
        if (self.translation_revision_id is not None
                and (not isinstance(self.translation_revision_id, str)
                     or not self.translation_revision_id.strip())):
            raise PaperInsightValidationError('번역 리비전 ID가 올바르지 않습니다.')


@dataclass(frozen=True)
class PaperInsight:
    insight_id: str
    position: int
    input: PaperInsightInput
    parse_revision_id: str


@dataclass(frozen=True)
class PaperInsightCollection:
    collection_id: str
    context_id: str
    scope: str
    items: Tuple[PaperInsight, ...]
    created_at: str
