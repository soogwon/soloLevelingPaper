"""단계별 논문 읽기 가이드(M1-06, #51)의 도메인 모델.

단계 목록은 learning_progress.LEARNING_STEPS를 그대로 쓴다.
사용자에게 보이는 이름·목표 문구는 이 파일 한 곳에서 관리한다.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

from solo_leveling.domain.learning_progress import LEARNING_STEPS

STEP_TITLES_KO = {
    'preview': '미리보기',
    'structure': '구조 파악',
    'deep_read': '정독',
    'review': '복습',
}

STEP_GOALS_KO = {
    'preview': '논문 전체의 윤곽을 잡습니다. 무엇을 풀려는 논문인지 먼저 확인하세요.',
    'structure': '논문이 어떤 순서로 전개되는지, 어떤 방법을 쓰는지 구조를 파악합니다.',
    'deep_read': '본문을 차례대로 꼼꼼히 읽고 방법과 결과를 이해합니다.',
    'review': '결과와 한계를 다시 확인하고 이미 아는 개념과 연결해 정리합니다.',
}

# 학습 목적(goal)별로 단계 목표 뒤에 덧붙이는 안내.
GOAL_HINTS_KO = {
    'understand': {},
    'implement': {
        'structure': '구현에 필요한 구성 요소와 입력·출력을 눈여겨보세요.',
        'deep_read': '구현에 필요한 수식, 설정값, 학습 조건을 놓치지 마세요.',
    },
    'skim': {
        'structure': '시간이 부족하면 이 단계까지만 읽어도 논문의 흐름을 알 수 있습니다.',
        'deep_read': '훑어보기가 목적이라면 핵심 섹션만 읽어도 됩니다.',
    },
}

STEP_STATUSES = ('done', 'current', 'upcoming')


@dataclass(frozen=True)
class StepGuide:
    step: str
    title_ko: str
    goal_ko: str
    status: str                      # done | current | upcoming
    section_ids: Tuple[str, ...]     # 이 단계에서 읽을 섹션(섹션 구분이 없으면 빈 목록)
    pdf_pages: Optional[Tuple[int, int]]  # (첫 페이지, 끝 페이지), PDF 기준
    key_concepts: Tuple[str, ...]    # 근거 연결 요약에서 가져온 핵심 항목. 요약이 없으면 빈 목록


@dataclass(frozen=True)
class ReadingGuide:
    context_id: str
    goal: str
    steps: Tuple[StepGuide, ...]
    current_step: str
    completed_steps: Tuple[str, ...]
    resume_position: Optional[dict]
    known_concepts: Tuple[str, ...]
    last_studied_at: Optional[str]

    @property
    def progress(self) -> float:
        return len(self.completed_steps) / len(LEARNING_STEPS)
