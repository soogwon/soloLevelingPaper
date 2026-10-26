"""퀴즈 시도·답안·채점 결과(M1-10, #55)의 도메인 모델.

시도는 제출 시점의 문항 내용(본문·선택지·정답·근거)을 그대로 복사해 둔다.
퀴즈를 다시 만들어 문항이 바뀌어도 과거 시도의 점수와 오답 근거를 다시 볼 수 있게 하기 위해서다.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.domain.quiz import QUESTION_TYPES


class AttemptValidationError(ValueError):
    """제출 형식이 맞지 않거나 채점 결과를 기록할 수 없다."""


@dataclass(frozen=True)
class AnswerResult:
    position: int                      # 퀴즈 안의 문항 위치(0부터)
    question_type: str
    prompt: str
    choices: Tuple[str, ...]           # 객관식 선택지, 단답형은 빈 튜플
    selected_index: Optional[int]      # 객관식에서 고른 위치. 무응답·단답형은 None
    submitted_text: Optional[str]      # 단답형 답안. 무응답·객관식은 None
    correct_answer: str
    grading_criteria: str
    is_correct: bool
    evidence_ids: Tuple[str, ...]      # 정답을 뒷받침하는 근거. 오답 근거 조회에 쓴다.

    def __post_init__(self):
        object.__setattr__(self, 'choices', tuple(self.choices))
        object.__setattr__(self, 'evidence_ids', tuple(self.evidence_ids))
        if self.question_type not in QUESTION_TYPES:
            raise AttemptValidationError(f'지원하지 않는 문항 유형: {self.question_type!r}')
        if not self.evidence_ids:
            raise AttemptValidationError('채점 결과에는 evidence_id가 하나 이상 필요합니다.')
        if not isinstance(self.is_correct, bool):
            raise AttemptValidationError('is_correct는 True/False여야 합니다.')

    @property
    def answered(self) -> bool:
        return self.selected_index is not None or bool((self.submitted_text or '').strip())


@dataclass(frozen=True)
class QuizAttempt:
    attempt_id: str
    context_id: str
    quiz_id: str                       # 제출 시점의 퀴즈. 퀴즈를 다시 만들어도 기록은 남는다.
    step: str
    results: Tuple[AnswerResult, ...]
    submitted_at: str

    def __post_init__(self):
        object.__setattr__(self, 'results', tuple(self.results))
        if self.step not in LEARNING_STEPS:
            raise AttemptValidationError(f'지원하지 않는 학습 단계: {self.step!r}')
        if not self.results:
            raise AttemptValidationError('채점 결과가 비어 있습니다.')

    @property
    def total_count(self) -> int:
        return len(self.results)

    @property
    def correct_count(self) -> int:
        return sum(1 for r in self.results if r.is_correct)

    @property
    def score(self) -> float:
        """0.0~1.0. 맞힌 문항 수 / 전체 문항 수. 무응답은 오답으로 센다."""
        return self.correct_count / self.total_count

    @property
    def wrong_results(self) -> Tuple[AnswerResult, ...]:
        return tuple(r for r in self.results if not r.is_correct)
