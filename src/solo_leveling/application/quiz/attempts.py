"""퀴즈 제출·채점·시도 조회(M1-10, #55).

- 객관식은 고른 선택지 위치가 정답 위치와 같은지로 채점한다.
- 단답형은 `ShortAnswerGrader`가 채점한다. 기본 채점기는 정답 문구와 정규화 후 완전히 같을 때만 정답으로 본다.
  채점 기준(grading_criteria)은 자연어라서, 문장형 답안까지 인정하려면 AI 채점기를 같은 자리에 끼우면 된다.
- 무응답은 오답으로 센다.
- 시도는 제출 시점의 문항 내용을 복사해 저장하므로, 퀴즈를 다시 만들어도 과거 점수와 오답 근거를 볼 수 있다.
"""
import unicodedata
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Protocol, Tuple, Union
from uuid import uuid4

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.domain.evidence_qa import EvidenceDetail
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.domain.models import now_iso
from solo_leveling.domain.quiz import QuizQuestion
from solo_leveling.domain.quiz_attempt import AnswerResult, AttemptValidationError, QuizAttempt
from solo_leveling.infrastructure.database import repository as repo

Answer = Union[int, str]  # 객관식은 고른 선택지 위치(0부터), 단답형은 답안 문자열

_EDGE_PUNCTUATION = ' \t\r\n.,;:!?"\'`“”‘’()[]{}<>'


def normalize_answer(text: str) -> str:
    """답안 비교용으로 다듬는다: 전각/반각 통일, 대소문자 무시, 앞뒤 문장부호와 모든 공백 제거.

    문장 안의 문장부호는 남긴다(예: '0.5'와 '05'는 다르다).
    """
    text = unicodedata.normalize('NFKC', text).casefold().strip(_EDGE_PUNCTUATION)
    return ''.join(text.split())


class ShortAnswerGrader(Protocol):
    def is_correct(self, question: QuizQuestion, submitted: str) -> bool:
        """단답형 답안이 정답인지 판정한다."""
        ...


class NormalizedAnswerGrader:
    """정답 문구와 정규화한 결과가 완전히 같을 때만 정답으로 본다(규칙 기반, 외부 호출 없음)."""

    def is_correct(self, question: QuizQuestion, submitted: str) -> bool:
        expected = normalize_answer(question.answer)
        return bool(expected) and normalize_answer(submitted) == expected


@dataclass(frozen=True)
class StepScore:
    step: str
    attempt_count: int
    latest_attempt_id: Optional[str]
    latest_correct: Optional[int]
    latest_total: Optional[int]
    latest_score: Optional[float]
    best_score: Optional[float]


@dataclass(frozen=True)
class ReviewItem:
    """틀린 문항의 복습 자료: 내 답, 정답, 채점 기준, 정답을 뒷받침하는 원문."""
    result: AnswerResult
    evidence: Tuple[EvidenceDetail, ...]


class QuizAttemptService:
    def __init__(self, db_path: str, grader: Optional[ShortAnswerGrader] = None,
                 id_factory: Callable[[], str] = lambda: str(uuid4()),
                 clock: Callable[[], str] = now_iso):
        self.db_path = db_path
        self.grader = grader if grader is not None else NormalizedAnswerGrader()
        self.new_id = id_factory
        self.clock = clock

    # ── 제출·채점 ───────────────────────────────────────────
    def submit(self, context_id: str, step: str, answers: Mapping[int, Answer]) -> QuizAttempt:
        """답안을 채점해 시도로 저장한다. answers의 키는 문항 위치(0부터)이며, 빠진 문항은 무응답이다."""
        if step not in LEARNING_STEPS:
            raise AttemptValidationError(f'지원하지 않는 학습 단계: {step!r}')
        if repo.get_learning_context(self.db_path, context_id) is None:
            raise ResourceNotFoundError('context not found')
        quiz = repo.get_quiz(self.db_path, context_id, step)
        if quiz is None:
            raise ResourceNotFoundError('이 단계의 퀴즈가 아직 없습니다.')
        self._validate(quiz.questions, answers)
        results = tuple(self._grade(position, question, answers.get(position))
                        for position, question in enumerate(quiz.questions))
        attempt = QuizAttempt(self.new_id(), context_id, quiz.quiz_id, step, results, self.clock())
        return repo.save_quiz_attempt(self.db_path, attempt)

    @staticmethod
    def _validate(questions, answers) -> None:
        if not isinstance(answers, Mapping) or not answers:
            raise AttemptValidationError('제출할 답안이 없습니다.')
        for position, value in answers.items():
            if isinstance(position, bool) or not isinstance(position, int) or not 0 <= position < len(questions):
                raise AttemptValidationError(f'없는 문항 위치: {position!r}')
            question = questions[position]
            if question.question_type == 'multiple_choice':
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < len(question.choices):
                    raise AttemptValidationError(f'{position}번 문항: 선택지 위치가 올바르지 않습니다.')
            elif not isinstance(value, str):
                raise AttemptValidationError(f'{position}번 문항: 단답형 답안은 문자열이어야 합니다.')

    def _grade(self, position: int, question: QuizQuestion, answer: Optional[Answer]) -> AnswerResult:
        selected = submitted = None
        correct = False
        if question.question_type == 'multiple_choice':
            selected = answer
            correct = answer is not None and answer == question.correct_index
        else:
            submitted = answer
            correct = bool(answer and answer.strip()) and bool(self.grader.is_correct(question, answer))
        return AnswerResult(position, question.question_type, question.prompt, question.choices, selected,
                            submitted, question.answer, question.grading_criteria, correct,
                            question.evidence_ids)

    # ── 조회 ────────────────────────────────────────────────
    def get_attempt(self, attempt_id: str) -> QuizAttempt:
        attempt = repo.get_quiz_attempt(self.db_path, attempt_id)
        if attempt is None:
            raise ResourceNotFoundError('attempt not found')
        return attempt

    def history(self, context_id: str, step: Optional[str] = None) -> Tuple[QuizAttempt, ...]:
        """과거 시도를 오래된 순서로 돌려준다."""
        if step is not None and step not in LEARNING_STEPS:
            raise AttemptValidationError(f'지원하지 않는 학습 단계: {step!r}')
        if repo.get_learning_context(self.db_path, context_id) is None:
            raise ResourceNotFoundError('context not found')
        return tuple(repo.list_quiz_attempts(self.db_path, context_id, step))

    def step_scores(self, context_id: str) -> Tuple[StepScore, ...]:
        """단계별 시도 횟수, 가장 최근 점수, 최고 점수. 시도가 없는 단계도 포함한다."""
        attempts = self.history(context_id)
        scores = []
        for step in LEARNING_STEPS:
            mine = [a for a in attempts if a.step == step]
            if not mine:
                scores.append(StepScore(step, 0, None, None, None, None, None))
                continue
            latest = mine[-1]
            scores.append(StepScore(step, len(mine), latest.attempt_id, latest.correct_count,
                                    latest.total_count, latest.score, max(a.score for a in mine)))
        return tuple(scores)

    def review(self, attempt_id: str) -> Tuple[ReviewItem, ...]:
        """틀린 문항마다 정답·채점 기준·정답을 뒷받침하는 원문을 묶어 돌려준다."""
        attempt = self.get_attempt(attempt_id)
        items = []
        for result in attempt.wrong_results:
            ids = list(dict.fromkeys(result.evidence_ids))
            items.append(ReviewItem(result, repo.get_evidences(self.db_path, attempt.context_id, ids).evidence))
        return tuple(items)
