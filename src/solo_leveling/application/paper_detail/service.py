"""저장된 요약·근거와 같은 학습 맥락의 기록을 조회한다."""
from dataclasses import asdict
from typing import Protocol
from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
from solo_leveling.domain.annotation import AnnotationPage, DEFAULT_PAGE_SIZE
from solo_leveling.domain.evidence_qa import require_text


class DetailReader(Protocol):
    def get(self, context_id: str, scope: str, offset: int, limit: int,
            summary_id: str | None) -> dict: ...

    def annotation_page(self, context_id: str, evidence_id: str, cursor: str | None) -> dict: ...


class AnnotationReader(Protocol):
    def list(self, context_id: str, *, limit: int) -> AnnotationPage: ...


class PaperDetailService:
    def __init__(self, reader: DetailReader, annotations: AnnotationReader | None = None):
        self.reader = reader
        self.annotations = annotations

    def get(self, context_id: str, *, scope: str = 'paper', offset: int = 0,
            limit: int = 20, summary_id: str | None = None) -> dict:
        try:
            require_text(context_id, 'context_id')
            require_text(scope, 'scope')
            if type(offset) is not int or offset < 0:
                raise ValueError('offset은 0 이상의 정수여야 합니다.')
            if type(limit) is not int or not 1 <= limit <= 50:
                raise ValueError('limit은 1~50의 정수여야 합니다.')
            if summary_id is not None:
                require_text(summary_id, 'summary_id')
            if offset and summary_id is None:
                raise ValueError('다음 페이지에는 summary_id가 필요합니다.')
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from None
        result = self.reader.get(context_id, scope, offset, limit, summary_id)
        if self.annotations is not None:
            # 요약 페이지/섹션 범위와 별개인 context 전체의 기록 첫 페이지다.
            # 저장 인용문은 스냅샷이며 현재 위치 확인(get)은 사용자 선택 시에만 한다.
            page = self.annotations.list(context_id, limit=DEFAULT_PAGE_SIZE)
            result = {**result, 'annotation_page': {
                'context_id': context_id, **asdict(page),
            }}
        return result

    def annotation_page(self, context_id: str, evidence_id: str, cursor: str | None = None) -> dict:
        return self.reader.annotation_page(context_id, evidence_id, cursor)
