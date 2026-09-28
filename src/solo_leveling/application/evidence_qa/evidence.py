"""재검색 없이 저장된 맥락의 근거를 조회한다."""

from typing import Protocol

from solo_leveling.domain.evidence_qa import GetEvidenceResult, require_text
from .errors import DataIntegrityError, InvalidArgumentError


class EvidenceReader(Protocol):
    def get(self, context_id: str, evidence_ids: tuple[str, ...]) -> GetEvidenceResult:
        """같은 DB 스냅샷에서 맥락·청크·인용문을 검증해 반환한다."""
        ...


class EvidenceService:
    def __init__(self, reader: EvidenceReader):
        self.reader = reader

    def get(self, context_id: str, evidence_ids: list[str] | tuple[str, ...]) -> GetEvidenceResult:
        try:
            require_text(context_id, 'context_id')
            if not isinstance(evidence_ids, (list, tuple)) or not evidence_ids:
                raise ValueError('근거 ID 목록이 필요합니다.')
            for evidence_id in evidence_ids:
                require_text(evidence_id, 'evidence_id')
            if len(set(evidence_ids)) != len(evidence_ids):
                raise ValueError('근거 ID가 중복됩니다.')
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from None
        ids = tuple(evidence_ids)
        result = self.reader.get(context_id, ids)
        if (not isinstance(result, GetEvidenceResult)
                or tuple(item.evidence_id for item in result.evidence) != ids):
            raise DataIntegrityError('조회한 근거가 요청 목록과 일치하지 않습니다.')
        return result
