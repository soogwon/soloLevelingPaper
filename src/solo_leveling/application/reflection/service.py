"""읽기 전·후 질문 제공과 답변 저장·조회·수정(M1-07 #52, M1-08 #53).

질문은 코드의 템플릿(domain.reflection)이고 답변만 DB에 저장한다. 답변은 학습 맥락(세션)에 붙으므로
서버를 다시 시작하거나 세션을 재개해도 같은 context_id로 다시 조회·수정할 수 있다.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

from solo_leveling.domain.reflection import (
    COMPARISON_GROUPS, PHASE_AFTER, PHASE_BEFORE, STATUS_COMPLETED, QuestionTemplate, ReflectionAnswer, templates_for,
)
from solo_leveling.infrastructure.database import repository as repo

# 세션 전체의 작성 상태
SESSION_NOT_STARTED = 'not_started'
SESSION_IN_PROGRESS = 'in_progress'
SESSION_COMPLETED = 'completed'
SESSION_NOT_APPLICABLE = 'not_applicable'


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
    status: str       # not_applicable / not_started / in_progress / completed


class ReflectionService:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_session(self, context_id: str, phase: str, step: Optional[str] = None) -> ReflectionSession:
        """질문 템플릿과 저장된 답변을 함께 돌려준다. step을 주면 그 학습 단계의 질문만 담는다."""
        templates = templates_for(phase, step)
        saved = repo.get_reflection_answers(self.db_path, context_id, phase)
        return _build_session(context_id, phase, templates, saved)

    def save_answer(self, context_id: str, phase: str, template_id: str, text: str,
                    status: str = 'draft') -> ReflectionSession:
        """답변을 저장(처음)하거나 고친(이미 있음) 뒤 세션의 현재 상태를 돌려준다."""
        repo.save_reflection_answer(self.db_path, context_id, phase, template_id, text, status)
        return self.get_session(context_id, phase)

    def get_comparison(self, context_id: str) -> 'ReflectionComparison':
        """읽기 전·후 답변을 비교 묶음(COMPARISON_GROUPS)별로 나란히 돌려준다(M1-08).

        두 시점의 답변을 한 번의 읽기로 가져와 같은 시점 기준으로 묶는다. 한쪽이 초안이거나 비어 있어도 다른 쪽
        답변은 그대로 보인다. 이해가 바뀌었는지, 한계가 해결됐는지는 판정하지 않고 사용자가 쓴 문구와 작성 상태만
        준다. 현재 템플릿에 없는 예전 ID의 답변은 묶음에 넣지 않는다(삭제하지도, 새 질문에 연결하지도 않는다).
        읽기 전 답변은 나중에 고칠 수 있으므로 비교 대상은 두 시점의 현재 저장본이다.
        """
        saved = repo.get_reflection_answers_all(self.db_path, context_id)
        sessions = {phase: _build_session(context_id, phase, templates_for(phase),
                                          [a for a in saved if a.phase == phase])
                    for phase in (PHASE_BEFORE, PHASE_AFTER)}
        items = {i.template.template_id: i for s in sessions.values() for i in s.items}
        groups = tuple(ComparisonGroupView(g.group_id, items[g.before_id], tuple(items[i] for i in g.after_ids))
                       for g in COMPARISON_GROUPS)
        return ReflectionComparison(context_id, groups, sessions[PHASE_BEFORE], sessions[PHASE_AFTER])


def _build_session(context_id: str, phase: str, templates: Tuple[QuestionTemplate, ...],
                   saved_answers) -> ReflectionSession:
    saved = {a.template_id: a for a in saved_answers}
    items = tuple(ReflectionItem(t, saved.get(t.template_id)) for t in templates)
    written = sum(1 for item in items if item.answer is not None)
    completed = sum(1 for item in items if item.answer is not None and item.answer.status == STATUS_COMPLETED)
    total = len(items)
    if not total:
        status = SESSION_NOT_APPLICABLE
    elif completed == total:
        status = SESSION_COMPLETED
    elif written:
        status = SESSION_IN_PROGRESS
    else:
        status = SESSION_NOT_STARTED
    return ReflectionSession(context_id, phase, items, total, written, completed, status)


@dataclass(frozen=True)
class ComparisonGroupView:
    group_id: str
    before: ReflectionItem                 # 읽기 전 질문과 현재 답변(없으면 answer=None → 미작성)
    after: Tuple[ReflectionItem, ...]      # 나란히 볼 읽기 후 질문과 현재 답변


@dataclass(frozen=True)
class ReflectionComparison:
    context_id: str
    groups: Tuple[ComparisonGroupView, ...]
    before: ReflectionSession              # 읽기 전 집계(written / completed / total)
    after: ReflectionSession               # 읽기 후 집계
