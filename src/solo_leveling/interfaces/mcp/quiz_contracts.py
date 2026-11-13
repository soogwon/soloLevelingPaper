"""퀴즈 MCP 도구의 입력 검증과 공개 응답 변환."""

from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from solo_leveling.domain.quiz import Quiz
from solo_leveling.application.quiz.attempts import ReviewItem, StepScore
from solo_leveling.domain.evidence_qa import EvidenceDetail
from solo_leveling.domain.quiz_attempt import QuizAttempt

Step = Literal['preview', 'structure', 'deep_read', 'review']
MAX_SUBMITTED_ANSWERS = 64
MAX_QUIZ_ANSWER_CHARS = 5000
MAX_SUBMISSION_BYTES = 64 * 1024
Text = Annotated[str, Field(strict=True, min_length=1, pattern=r'\S')]
Index = Annotated[int, Field(strict=True, ge=0)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)


class QuizInput(ContractModel):
    context_id: Text
    step: Step


class ChoiceAnswer(ContractModel):
    question_index: Index
    selected_index: Index


class TextAnswer(ContractModel):
    question_index: Index
    text: Annotated[str, Field(max_length=MAX_QUIZ_ANSWER_CHARS)]


class SubmitInput(QuizInput):
    quiz_id: Text
    request_key: Annotated[Text, Field(max_length=128)]
    answers: Annotated[list[ChoiceAnswer | TextAnswer], Field(min_length=1, max_length=MAX_SUBMITTED_ANSWERS)]

    @field_validator('request_key')
    @classmethod
    def printable_request_key(cls, value: str) -> str:
        if not value.isprintable():
            raise ValueError('요청 키에는 출력 가능한 문자만 사용할 수 있습니다.')
        return value

    @model_validator(mode='after')
    def validate_submission(self):
        # 긴 식별자를 다시 직렬화하기 전에 전체 바이트 한도를 넘는 경우를 먼저 거른다.
        if len(self.context_id) + len(self.quiz_id) > MAX_SUBMISSION_BYTES:
            raise ValueError('퀴즈 제출 크기를 초과했습니다.')
        positions = [answer.question_index for answer in self.answers]
        if len(positions) != len(set(positions)):
            raise ValueError('문항 위치는 중복될 수 없습니다.')
        # 전송 시 공백·이스케이프 표기 차이와 무관하게 검증된 입력의 UTF-8 JSON 크기를 센다.
        if len(self.model_dump_json().encode('utf-8')) > MAX_SUBMISSION_BYTES:
            raise ValueError('퀴즈 제출 크기를 초과했습니다.')
        return self

    def answer_mapping(self) -> dict[int, int | str]:
        """문항 순서만 통일하며 문자열·요청 키는 정규화하지 않는다."""
        return {
            answer.question_index: (
                answer.selected_index if isinstance(answer, ChoiceAnswer) else answer.text
            )
            for answer in sorted(self.answers, key=lambda answer: answer.question_index)
        }


class AttemptInput(ContractModel):
    context_id: Text
    attempt_id: Text


class ScoresInput(ContractModel):
    context_id: Text


class ScoresOutput(ScoresInput):
    scores: list[StepScore]


class HistoryInput(ContractModel):
    context_id: Text
    step: Step | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 20
    cursor: Text | None = None


class PublicQuestion(ContractModel):
    question_index: Index
    question_type: Literal['multiple_choice', 'short_answer']
    prompt: Text
    choices: list[str]


class PublicQuiz(QuizInput):
    quiz_id: Text
    grading_mode: Literal['normalized_exact_match'] = 'normalized_exact_match'
    questions: list[PublicQuestion]


class QuestionResult(ContractModel):
    question_index: Index
    question_type: Literal['multiple_choice', 'short_answer']
    prompt: Text
    choices: list[str]
    selected_index: Index | None
    submitted_text: str | None
    correct_answer: str
    grading_criteria: str
    is_correct: bool
    evidence_ids: list[str]


class AttemptSummary(QuizInput):
    attempt_id: Text
    quiz_id: Text
    submitted_at: Text
    correct_count: Index
    total_count: Annotated[int, Field(strict=True, ge=1)]
    score: Annotated[float, Field(ge=0, le=1)]


class AttemptOutput(AttemptSummary):
    results: list[QuestionResult]


class ReviewQuestion(ContractModel):
    result: QuestionResult
    evidence: list[EvidenceDetail]


class ReviewOutput(AttemptInput):
    items: list[ReviewQuestion]


class SubmitOutput(AttemptOutput):
    reused: bool


class HistoryOutput(ContractModel):
    context_id: Text
    items: list[AttemptSummary]
    next_cursor: Text | None


@dataclass(frozen=True)
class InputPolicy:
    """미확정 한도는 호출자가 명시하며 운영 기본값으로 확정하지 않는다."""

    max_answers: int
    max_text_length: int
    reject_blank_text: bool

    def __post_init__(self):
        for value in (self.max_answers, self.max_text_length):
            if type(value) is not int or value < 1:
                raise ValueError('입력 한도는 양의 정수여야 합니다.')
        if type(self.reject_blank_text) is not bool:
            raise ValueError('빈 답안 정책을 명시해야 합니다.')


@dataclass(frozen=True)
class SubmissionResult:
    attempt: QuizAttempt
    reused: bool


class QuizServicePort(Protocol):
    """B 연결 시 맞출 초안. 저장 원자성·중복 방어는 서비스/저장소 책임이다."""

    def get_quiz(self, context_id: str, step: str) -> Quiz: ...

    def generate_quiz(self, context_id: str, step: str) -> Quiz: ...

    def submit(self, *, context_id: str, step: str, quiz_id: str,
               request_key: str, answers: dict[int, int | str]) -> SubmissionResult: ...

    def get_attempt(self, *, context_id: str, attempt_id: str) -> QuizAttempt: ...

    def history(self, context_id: str, step: str | None) -> tuple[QuizAttempt, ...]: ...

    def history_page(self, context_id: str, step: str | None, limit: int, cursor: str | None): ...

    def scores(self, context_id: str) -> tuple[StepScore, ...]: ...

    def review(self, context_id: str, attempt_id: str) -> tuple[ReviewItem, ...]: ...


class ContractError(Exception):
    """입력 본문·서비스 예외 원문 없이 고정 코드만 경계 밖으로 전달한다."""

    def __init__(self, code: str):
        allowed = {'INVALID_ARGUMENT', 'NOT_FOUND', 'QUIZ_NOT_READY', 'QUIZ_CHANGED',
                   'REQUEST_CONFLICT', 'UPSTREAM_UNAVAILABLE', 'INTERNAL_ERROR'}
        self.code = code if code in allowed else 'INTERNAL_ERROR'
        super().__init__(self.code)


class QuizContractAdapter:
    """입력을 검증하고 서비스의 저장 결과를 공개 응답으로 변환한다."""

    def __init__(self, service: QuizServicePort, policy: InputPolicy | None = None):
        self.service, self.policy = service, policy

    @staticmethod
    def _input(model, payload):
        try:
            return model.model_validate(payload)
        except ValidationError:
            raise ContractError('INVALID_ARGUMENT') from None

    @staticmethod
    def _call(function, **arguments):
        try:
            return function(**arguments)
        except ContractError:
            raise
        except Exception:
            raise ContractError('INTERNAL_ERROR') from None

    def get_quiz(self, payload) -> PublicQuiz:
        request = self._input(QuizInput, payload)
        quiz = self._call(self.service.get_quiz, context_id=request.context_id, step=request.step)
        if not isinstance(quiz, Quiz):
            raise ContractError('INTERNAL_ERROR')
        if (quiz.context_id, quiz.step) != (request.context_id, request.step):
            raise ContractError('INTERNAL_ERROR')
        # 정답·채점 기준·근거를 포함하는 도메인 객체 전체를 직렬화하지 않는다.
        return self._call(serialize_quiz, quiz=quiz)

    def generate_quiz(self, payload) -> PublicQuiz:
        request = self._input(QuizInput, payload)
        quiz = self._call(
            self.service.generate_quiz, context_id=request.context_id, step=request.step)
        if not isinstance(quiz, Quiz):
            raise ContractError('INTERNAL_ERROR')
        if (quiz.context_id, quiz.step) != (request.context_id, request.step):
            raise ContractError('INTERNAL_ERROR')
        return self._call(serialize_quiz, quiz=quiz)

    def submit(self, payload) -> SubmitOutput:
        request = self._input(SubmitInput, payload)
        if self.policy and len(request.answers) > self.policy.max_answers:
            raise ContractError('INVALID_ARGUMENT')
        for answer in request.answers:
            if self.policy and isinstance(answer, TextAnswer) and (
                len(answer.text) > self.policy.max_text_length
                or (self.policy.reject_blank_text and not answer.text.strip())
            ):
                raise ContractError('INVALID_ARGUMENT')
        # 현재 퀴즈를 사전 조회하지 않아 재생성 후 완료 요청의 재사용을 방해하지 않는다.
        result = self._call(self.service.submit, context_id=request.context_id, step=request.step,
                                     quiz_id=request.quiz_id, request_key=request.request_key,
                                     answers=request.answer_mapping())
        if not isinstance(result, SubmissionResult) or not isinstance(result.attempt, QuizAttempt):
            raise ContractError('INTERNAL_ERROR')
        attempt = result.attempt
        if (attempt.context_id, attempt.step, attempt.quiz_id) != (
            request.context_id, request.step, request.quiz_id
        ):
            raise ContractError('INTERNAL_ERROR')
        output = self._call(serialize_attempt, attempt=attempt)
        return self._call(SubmitOutput, **output.model_dump(), reused=result.reused)

    def get_attempt(self, payload) -> AttemptOutput:
        request = self._input(AttemptInput, payload)
        attempt = self._call(self.service.get_attempt, context_id=request.context_id, attempt_id=request.attempt_id)
        if not isinstance(attempt, QuizAttempt):
            raise ContractError('INTERNAL_ERROR')
        if (attempt.context_id, attempt.attempt_id) != (request.context_id, request.attempt_id):
            raise ContractError('NOT_FOUND')
        return self._call(serialize_attempt, attempt=attempt)

    def scores(self, payload) -> ScoresOutput:
        request = self._input(ScoresInput, payload)
        scores = self._call(self.service.scores, context_id=request.context_id)
        return ScoresOutput(context_id=request.context_id, scores=list(scores))

    def review(self, payload) -> ReviewOutput:
        request = self._input(AttemptInput, payload)
        items = self._call(self.service.review, context_id=request.context_id, attempt_id=request.attempt_id)
        return ReviewOutput(**request.model_dump(), items=[
            ReviewQuestion(result=serialize_question_result(item.result), evidence=list(item.evidence))
            for item in items
        ])

    def list_attempts(self, payload) -> HistoryOutput:
        request = self._input(HistoryInput, payload)
        page = self._call(self.service.history_page, context_id=request.context_id, step=request.step,
                          limit=request.limit, cursor=request.cursor)
        if any(a.context_id != request.context_id or (request.step and a.step != request.step)
               for a in page.items):
            raise ContractError('INTERNAL_ERROR')
        return HistoryOutput(
            context_id=request.context_id,
            items=[AttemptSummary.model_validate(a, from_attributes=True) for a in page.items],
            next_cursor=page.next_cursor,
        )


def serialize_quiz(quiz: Quiz) -> PublicQuiz:
    """제출 전에는 공개 문항 필드만 반환한다."""
    return PublicQuiz(
        context_id=quiz.context_id, step=quiz.step, quiz_id=quiz.quiz_id,
        questions=[PublicQuestion(question_index=i, question_type=q.question_type,
                                  prompt=q.prompt, choices=list(q.choices))
                   for i, q in enumerate(quiz.questions)],
    )


def serialize_attempt(attempt: QuizAttempt) -> AttemptOutput:
    """현재 문항이 아닌 제출 당시 결과 스냅샷을 반환한다."""
    return AttemptOutput(
        attempt_id=attempt.attempt_id, context_id=attempt.context_id, quiz_id=attempt.quiz_id,
        step=attempt.step, submitted_at=attempt.submitted_at, correct_count=attempt.correct_count,
        total_count=attempt.total_count, score=attempt.score,
        results=[serialize_question_result(result) for result in attempt.results],
    )


def serialize_question_result(result) -> QuestionResult:
    return QuestionResult(
        question_index=result.position, question_type=result.question_type, prompt=result.prompt,
        choices=list(result.choices), selected_index=result.selected_index, submitted_text=result.submitted_text,
        correct_answer=result.correct_answer, grading_criteria=result.grading_criteria,
        is_correct=result.is_correct, evidence_ids=list(result.evidence_ids),
    )
