"""읽기 가이드의 핵심 개념을 근거 연결형 요약(M1-05)에서 가져온다.

저장된 전체(paper) 요약의 항목을 단계에 연결한다. 요약이 없으면 비워 두며 요약을 새로 만들지 않는다.
  claim(주장) -> preview, method(방법) -> structure, result(결과) -> deep_read, limitation(한계) -> review
"""
from typing import Dict, List

from solo_leveling.domain.paper_summary import PAPER_SCOPE
from solo_leveling.infrastructure.database import repository as repo

KIND_TO_STEP = {
    'claim': 'preview',
    'method': 'structure',
    'result': 'deep_read',
    'limitation': 'review',
}
MAX_PER_STEP = 5  # 한 단계에 보여 줄 핵심 항목 수


class SummaryKeyConceptSource:
    def __init__(self, db_path: str, max_per_step: int = MAX_PER_STEP):
        if max_per_step < 1:
            raise ValueError('max_per_step must be positive')
        self.db_path = db_path
        self.max_per_step = max_per_step

    def key_concepts(self, context_id: str) -> Dict[str, List[str]]:
        summary = repo.get_summary(self.db_path, context_id, PAPER_SCOPE)
        if summary is None:
            return {}
        concepts: Dict[str, List[str]] = {}
        for item in summary.items:
            texts = concepts.setdefault(KIND_TO_STEP[item.kind], [])
            if len(texts) < self.max_per_step:
                texts.append(item.text)
        return concepts
