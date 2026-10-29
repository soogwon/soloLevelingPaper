"""읽기 전·후 질문 제공과 답변 저장·조회·수정(M1-07 #52, M1-08 #53).

질문은 코드의 템플릿(domain.reflection)이고 답변만 DB에 저장한다. 답변은 학습 맥락(세션)에 붙으므로
서버를 다시 시작하거나 세션을 재개해도 같은 context_id로 다시 조회·수정할 수 있다.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

from solo_leveling.domain.reflection import (
    STATUS_COMPLETED, QuestionTemplate, ReflectionAnswer, templates_for,
)
from solo_leveling.infrastructure.database import repository as repo

# 세션 전체의 작성 상태
SESSION_NOT_STARTED = 'not_started'
SESSION_IN_PROGRESS = 'in_progress'
SESSION_COMPLETED = 'completed'


@dataclass(frozen=True)
class ReflectionItem:
    template: QuestionTemplate
    answer: Optional[ReflectionAnswer]  # 아직 쓰지 않았으면 None


@dataclass(frozen=True)
class ReflectionSession:
    context_id: str
    phase: str
    items: Tuple[ReflectionItem, ...]
    total: int
    written: int      # 답변이 저장된(작성 중 포함) 질문 수
    completed: int    # 작성 완료로 표시한 질문 수
    status: str       # not_started / in_progress / completed


class ReflectionService:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_session(self, context_id: str, phase: str, step: Optional[str] = None) -> ReflectionSession:
        """질문 템플릿과 저장된 답변을 함께 돌려준다. step을 주면 그 학습 단계의 질문만 담는다."""
        templates = templates_for(phase, step)
        saved = {a.template_id: a for a in repo.get_reflection_answers(self.db_path, context_id, phase)}
        items = tuple(ReflectionItem(t, saved.get(t.template_id)) for t in templates)
        written = sum(1 for item in items if item.answer is not None)
        completed = sum(1 for item in items if item.answer is not None and item.answer.status == STATUS_COMPLETED)
        total = len(items)
        if total and completed == total:
            status = SESSION_COMPLETED
        elif written:
            status = SESSION_IN_PROGRESS
        else:
            status = SESSION_NOT_STARTED
        return ReflectionSession(context_id, phase, items, total, written, completed, status)

    def save_answer(self, context_id: str, phase: str, template_id: str, text: str,
                    status: str = 'draft') -> ReflectionSession:
        """답변을 저장(처음)하거나 고친(이미 있음) 뒤 세션의 현재 상태를 돌려준다."""
        repo.save_reflection_answer(self.db_path, context_id, phase, template_id, text, status)
        return self.get_session(context_id, phase)
