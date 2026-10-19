"""학습 맥락(context)별 진행 상태.

context는 논문 버전·번역·색인을 고정해 두는 값이고, 학습 진행은 계속 바뀌는 값이라
별도로 저장한다. 같은 context_id로 조회하면 이전 학습 위치를 다시 불러온다.
"""
from dataclasses import dataclass, field
from typing import Optional

# 학습 단계 목록과 순서. M1-06(단계별 읽기 가이드)와 같은 목록을 쓴다.
# 단계 이름을 바꿀 때는 이 한 곳만 고친다.
LEARNING_STEPS: tuple[str, ...] = ('preview', 'structure', 'deep_read', 'review')

# 재개 위치로 저장할 수 있는 항목. 값은 문자열 또는 정수만 허용한다.
RESUME_POSITION_KEYS: frozenset[str] = frozenset({'chunk_id', 'section_id', 'pdf_page'})


@dataclass(frozen=True)
class LearningProgress:
    context_id: str
    current_step: str = LEARNING_STEPS[0]
    completed_steps: list = field(default_factory=list)
    resume_position: Optional[dict] = None
    last_studied_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def progress(self) -> float:
        """완료한 단계 수 / 전체 단계 수 (0.0~1.0)."""
        return len(self.completed_steps) / len(LEARNING_STEPS)
