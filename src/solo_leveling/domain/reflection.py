"""읽기 전·후 질문 템플릿과 답변(M1-07 #52, M1-08 #53).

- 질문은 코드에 고정된 템플릿이다(AI가 만들지 않는다). 읽기 전 질문은 배경지식·가설·기대 내용을 묻는다.
- 답변은 학습 맥락(context = 학습 세션)에 붙고, 템플릿마다 하나씩 저장한다. 다시 쓰면 같은 답변을 고친다.
- 작성 상태는 draft(작성 중)와 completed(작성 완료) 두 가지다. 본문이 비어 있으면 completed로 저장할 수 없다.
- 읽기 후 템플릿과 전후 비교는 #53에서 같은 틀에 추가한다.
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

# 시점별 템플릿 목록. #53에서 'after' 항목을 채운다.
TEMPLATES: dict[str, tuple[QuestionTemplate, ...]] = {
    PHASE_BEFORE: BEFORE_READING_TEMPLATES,
    PHASE_AFTER: (),
}


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
