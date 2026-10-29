"""저장된 요약과 근거만 조회하는 논문 상세 서비스."""
from typing import Protocol
from solo_leveling.application.evidence_qa.errors import InvalidArgumentError
from solo_leveling.domain.evidence_qa import require_text


class DetailReader(Protocol):
    def get(self, context_id: str, scope: str, offset: int, limit: int,
            summary_id: str | None) -> dict: ...


class PaperDetailService:
    def __init__(self, reader: DetailReader):
        self.reader = reader

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
        return self.reader.get(context_id, scope, offset, limit, summary_id)
