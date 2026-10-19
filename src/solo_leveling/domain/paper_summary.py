"""근거 연결형 논문 요약(M1-05, #50)의 도메인 모델.

요약은 항목(주장·방법·결과·한계)의 목록이고, 모든 항목은 evidence_id를 하나 이상 가진다.
근거가 없는 항목은 검증된 요약으로 저장하지 않는다(저장소가 거부한다).
"""
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

SUMMARY_KINDS = ('claim', 'method', 'result', 'limitation')
PAPER_SCOPE = 'paper'  # 전체 요약의 scope. 섹션 요약은 section_id를 scope로 쓴다.


class SummaryValidationError(ValueError):
    """근거가 없거나 형식이 맞지 않아 검증된 요약으로 저장할 수 없다."""


@dataclass(frozen=True)
class SummaryItem:
    kind: str
    text: str
    evidence_ids: Tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, 'evidence_ids', tuple(self.evidence_ids))
        if self.kind not in SUMMARY_KINDS:
            raise SummaryValidationError(f'지원하지 않는 요약 항목 종류: {self.kind!r}')
        if not isinstance(self.text, str) or not self.text.strip():
            raise SummaryValidationError('요약 항목 본문이 비어 있습니다.')
        if not self.evidence_ids or any(not isinstance(e, str) or not e for e in self.evidence_ids):
            raise SummaryValidationError('요약 항목에는 evidence_id가 하나 이상 필요합니다.')


@dataclass(frozen=True)
class PaperSummary:
    summary_id: str
    context_id: str
    scope: str  # PAPER_SCOPE 또는 section_id
    items: Tuple[SummaryItem, ...]
    created_at: str


def split_supported(items: Iterable[SummaryItem], known_evidence_ids: Iterable[str]
                    ) -> Tuple[Tuple[SummaryItem, ...], Tuple[SummaryItem, ...]]:
    """생성기가 만든 항목을 (근거가 확인된 항목, 근거를 확인할 수 없는 항목)으로 나눈다."""
    known = set(known_evidence_ids)
    supported, unsupported = [], []
    for item in items:
        (supported if all(e in known for e in item.evidence_ids) else unsupported).append(item)
    return tuple(supported), tuple(unsupported)
