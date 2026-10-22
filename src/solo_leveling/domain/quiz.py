"""근거 기반 단계별 퀴즈(M1-09, #54)의 도메인 모델.

퀴즈는 학습 단계(LEARNING_STEPS) 하나에 대한 문항 목록이다.
모든 문항은 정답(객관식은 정답 선택지, 단답형은 정답 + 채점 기준)과 evidence_id를 가진다.
정답이나 근거가 없는 문항은 검증된 퀴즈로 저장하지 않는다(저장소가 거부한다).
"""
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

QUESTION_TYPES = ('multiple_choice', 'short_answer')
MIN_CHOICES = 2
MAX_CHOICES = 6


class QuizValidationError(ValueError):
    """정답이나 근거가 없거나 형식이 맞지 않아 검증된 퀴즈로 저장할 수 없다."""


def _blank(value) -> bool:
    return not isinstance(value, str) or not value.strip()


@dataclass(frozen=True)
class QuizQuestion:
    question_type: str
    prompt: str
    evidence_ids: Tuple[str, ...]
    choices: Tuple[str, ...] = ()          # 객관식 선택지. 단답형은 비워 둔다.
    correct_index: Optional[int] = None    # 객관식 정답 위치(0부터). 단답형은 None.
    answer: str = ''                       # 정답 문구. 객관식은 선택지에서 자동으로 채운다.
    grading_criteria: str = ''             # 단답형 채점 기준. 객관식은 비워도 된다.

    def __post_init__(self):
        object.__setattr__(self, 'evidence_ids', tuple(self.evidence_ids))
        object.__setattr__(self, 'choices', tuple(self.choices))
        if self.question_type not in QUESTION_TYPES:
            raise QuizValidationError(f'지원하지 않는 문항 유형: {self.question_type!r}')
        if _blank(self.prompt):
            raise QuizValidationError('문항 본문이 비어 있습니다.')
        if not self.evidence_ids or any(not isinstance(e, str) or not e for e in self.evidence_ids):
            raise QuizValidationError('문항에는 evidence_id가 하나 이상 필요합니다.')
        if self.question_type == 'multiple_choice':
            self._validate_multiple_choice()
        else:
            self._validate_short_answer()

    def _validate_multiple_choice(self):
        if not MIN_CHOICES <= len(self.choices) <= MAX_CHOICES:
            raise QuizValidationError(f'객관식 선택지는 {MIN_CHOICES}~{MAX_CHOICES}개여야 합니다.')
        if any(_blank(c) for c in self.choices):
            raise QuizValidationError('비어 있는 선택지가 있습니다.')
        if len({c.strip() for c in self.choices}) != len(self.choices):
            raise QuizValidationError('중복된 선택지가 있습니다.')
        index = self.correct_index
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.choices):
            raise QuizValidationError('객관식에는 선택지 안의 정답 위치가 필요합니다.')
        object.__setattr__(self, 'answer', self.choices[index])

    def _validate_short_answer(self):
        if self.choices or self.correct_index is not None:
            raise QuizValidationError('단답형에는 선택지나 정답 위치를 둘 수 없습니다.')
        if _blank(self.answer):
            raise QuizValidationError('단답형에는 정답이 필요합니다.')
        if _blank(self.grading_criteria):
            raise QuizValidationError('단답형에는 채점 기준이 필요합니다.')


@dataclass(frozen=True)
class Quiz:
    quiz_id: str
    context_id: str
    step: str  # LEARNING_STEPS 중 하나
    questions: Tuple[QuizQuestion, ...]
    created_at: str


def split_supported(questions: Iterable[QuizQuestion], known_evidence_ids: Iterable[str]
                    ) -> Tuple[Tuple[QuizQuestion, ...], Tuple[QuizQuestion, ...]]:
    """생성기가 만든 문항을 (근거가 확인된 문항, 근거를 확인할 수 없는 문항)으로 나눈다."""
    known = set(known_evidence_ids)
    supported, unsupported = [], []
    for question in questions:
        (supported if all(e in known for e in question.evidence_ids) else unsupported).append(question)
    return tuple(supported), tuple(unsupported)
