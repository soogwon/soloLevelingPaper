"""읽기 전·후 질문 템플릿과 답변(M1-07 #52, M1-08 #53).

- 질문은 코드에 고정된 템플릿이다(AI가 만들지 않는다). 읽기 전 질문은 배경지식·가설·기대 내용을 묻는다.
- 답변은 학습 맥락(context = 학습 세션)에 붙고, 템플릿마다 하나씩 저장한다. 다시 쓰면 같은 답변을 고친다.
- 작성 상태는 draft(작성 중)와 completed(작성 완료) 두 가지다. 본문이 비어 있으면 completed로 저장할 수 없다.
- 읽기 후 질문은 주장·증거·한계·적용 네 가지다(M1-08, #53). 전후 비교는 읽기 전 질문 하나와 읽기 후 질문 하나 이상을
  묶음(COMPARISON_GROUPS)으로 연결해 두 시점의 현재 저장본을 나란히 보여 주는 것이다. 이해가 바뀌었는지, 한계가
  해결됐는지는 코드가 판정하지 않고 사용자가 쓴 문구와 작성 상태만 보여 준다. 묶음은 코드의 고정 질문 ID로만
  연결하고 DB에 저장하지 않으므로, 질문 순서나 문구가 바뀌어도 저장된 답변은 영향을 받지 않는다. 질문 ID는 질문의
  의미에 고정한다(의미가 달라지면 새 ID를 쓴다). QuestionTemplate의 모양(MCP로 나가는 필드)은 바꾸지 않으려고
  필드를 더하지 않고 묶음을 별도 표로 둔다.
"""
from dataclasses import dataclass
from typing import Optional

from solo_leveling.domain.learning_progress import LEARNING_STEPS

PHASE_BEFORE = 'before'
PHASE_AFTER = 'after'
PHASES: tuple[str, ...] = (PHASE_BEFORE, PHASE_AFTER)

STATUS_DRAFT = 'draft'
STATUS_COMPLETED = 'completed'
ANSWER_STATUSES: tuple[str, ...] = (STATUS_DRAFT, STATUS_COMPLETED)

MAX_ANSWER_LENGTH = 5000


class ReflectionValidationError(ValueError):
    """질문 템플릿이나 답변 형식이 맞지 않는다."""


@dataclass(frozen=True)
class QuestionTemplate:
    template_id: str
    phase: str
    category: str
    prompt: str
    step: str  # 이 질문을 보여 줄 학습 단계(LEARNING_STEPS 중 하나)

    def __post_init__(self):
        if self.phase not in PHASES:
            raise ReflectionValidationError(f'지원하지 않는 질문 시점: {self.phase!r}')
        if self.step not in LEARNING_STEPS:
            raise ReflectionValidationError(f'지원하지 않는 학습 단계: {self.step!r}')
        if not self.template_id.strip() or not self.prompt.strip():
            raise ReflectionValidationError('질문 ID와 문구가 필요합니다.')


# 읽기 전 질문은 학습 첫 단계(preview)에서 묻는다. 순서가 화면 순서다.
BEFORE_READING_TEMPLATES: tuple[QuestionTemplate, ...] = (
    QuestionTemplate('before.background', PHASE_BEFORE, 'background',
                     '이 논문의 주제와 관련해 이미 알고 있는 배경지식은 무엇인가요?', 'preview'),
    QuestionTemplate('before.hypothesis', PHASE_BEFORE, 'hypothesis',
                     '이 논문이 풀려는 문제와 그 해결 방법에 대해 어떤 가설을 가지고 있나요?', 'preview'),
    QuestionTemplate('before.expectation', PHASE_BEFORE, 'expectation',
                     '이 논문에서 무엇을 얻고 싶고, 어떤 내용이 나올 것으로 기대하나요?', 'preview'),
)

# 읽기 후 질문은 마지막 단계(review)에서 묻는다. 순서가 화면 순서다.
AFTER_READING_TEMPLATES: tuple[QuestionTemplate, ...] = (
    QuestionTemplate('after.claim', PHASE_AFTER, 'claim',
                     '논문의 핵심 주장은 무엇인가요? 읽기 전에 세운 가설과 비교해 무엇이 같거나 달랐나요?', 'review'),
    QuestionTemplate('after.evidence', PHASE_AFTER, 'evidence',
                     '그 주장을 뒷받침하는 실험이나 관찰을 하나 고른다면 무엇인가요? 그 결과가 왜 근거가 된다고 생각하나요?',
                     'review'),
    QuestionTemplate('after.limitation', PHASE_AFTER, 'limitation',
                     '읽기 전부터 알고 있던 내용 중 적용 범위를 다시 생각하게 된 부분이나, 아직 확인하고 싶은 한계는 무엇인가요?',
                     'review'),
    QuestionTemplate('after.application', PHASE_AFTER, 'application',
                     '읽기 전에 기대한 것을 바탕으로, 배운 내용을 어디에 어떻게 적용해 보고 싶나요?', 'review'),
)


@dataclass(frozen=True)
class ComparisonGroup:
    """전후 비교 묶음: 읽기 전 질문 하나와 그것과 나란히 볼 읽기 후 질문들(순서가 화면 순서)."""
    group_id: str
    before_id: str
    after_ids: tuple[str, ...]


COMPARISON_GROUPS: tuple[ComparisonGroup, ...] = (
    ComparisonGroup('hypothesis_vs_paper', 'before.hypothesis', ('after.claim', 'after.evidence')),
    ComparisonGroup('background_vs_limitation', 'before.background', ('after.limitation',)),
    ComparisonGroup('expectation_vs_application', 'before.expectation', ('after.application',)),
)

# 시점별 템플릿 목록.
TEMPLATES: dict[str, tuple[QuestionTemplate, ...]] = {
    PHASE_BEFORE: BEFORE_READING_TEMPLATES,
    PHASE_AFTER: AFTER_READING_TEMPLATES,
}


def _check_templates() -> None:
    """템플릿 정의의 일관성을 불러올 때 한 번 확인한다: ID 유일, 시점 일치, 묶음이 실제 질문을 한 번씩만 가리킴."""
    seen: set[str] = set()
    for phase, templates in TEMPLATES.items():
        for template in templates:
            if template.phase != phase or template.template_id in seen:
                raise ReflectionValidationError(f'템플릿 정의가 올바르지 않습니다: {template.template_id!r}')
            seen.add(template.template_id)
    before_ids = [g.before_id for g in COMPARISON_GROUPS]
    after_ids = [i for g in COMPARISON_GROUPS for i in g.after_ids]
    ok = (len({g.group_id for g in COMPARISON_GROUPS}) == len(COMPARISON_GROUPS)
          and len(set(before_ids)) == len(before_ids) and len(set(after_ids)) == len(after_ids)
          and set(before_ids) == {t.template_id for t in BEFORE_READING_TEMPLATES}
          and set(after_ids) == {t.template_id for t in AFTER_READING_TEMPLATES}
          and all(g.after_ids for g in COMPARISON_GROUPS))
    if not ok:
        raise ReflectionValidationError('전후 비교 묶음이 올바르지 않습니다.')


_check_templates()


def templates_for(phase: str, step: Optional[str] = None) -> tuple[QuestionTemplate, ...]:
    """시점(과 선택적으로 학습 단계)에 해당하는 질문 템플릿을 화면 순서대로 돌려준다."""
    if phase not in PHASES:
        raise ReflectionValidationError(f'지원하지 않는 질문 시점: {phase!r}')
    if step is not None and step not in LEARNING_STEPS:
        raise ReflectionValidationError(f'지원하지 않는 학습 단계: {step!r}')
    return tuple(t for t in TEMPLATES[phase] if step is None or t.step == step)


def get_template(phase: str, template_id: str) -> QuestionTemplate:
    for template in templates_for(phase):
        if template.template_id == template_id:
            return template
    raise ReflectionValidationError(f'이 시점에 없는 질문입니다: {template_id!r}')


def validate_answer(phase: str, template_id: str, text: str, status: str) -> QuestionTemplate:
    """답변 형식을 검사하고 해당 템플릿을 돌려준다."""
    template = get_template(phase, template_id)
    if status not in ANSWER_STATUSES:
        raise ReflectionValidationError(f'지원하지 않는 작성 상태: {status!r}')
    if not isinstance(text, str):
        raise ReflectionValidationError('답변은 문자열이어야 합니다.')
    if len(text) > MAX_ANSWER_LENGTH:
        raise ReflectionValidationError(f'답변은 {MAX_ANSWER_LENGTH}자까지 쓸 수 있습니다.')
    if status == STATUS_COMPLETED and not text.strip():
        raise ReflectionValidationError('내용이 없는 답변은 작성 완료로 저장할 수 없습니다.')
    return template


@dataclass(frozen=True)
class ReflectionAnswer:
    context_id: str
    phase: str
    template_id: str
    text: str
    status: str
    created_at: str
    updated_at: str
