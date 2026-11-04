"""퀴즈 시도·답안·채점 결과(M1-10, #55)의 도메인 모델.

시도는 제출 시점의 문항 내용(본문·선택지·정답·근거)을 그대로 복사해 둔다.
퀴즈를 다시 만들어 문항이 바뀌어도 과거 시도의 점수와 오답 근거를 다시 볼 수 있게 하기 위해서다.
"""
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Tuple, Union

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


# ── 시도 목록(#145) ────────────────────────────────────────────
MAX_HISTORY_PAGE = 100
DEFAULT_HISTORY_PAGE = 20


@dataclass(frozen=True)
class QuizAttemptSummary:
    """목록용 시도 요약. 개별 답안·정답·근거 본문은 담지 않는다."""
    attempt_id: str
    context_id: str
    quiz_id: str
    step: str
    correct_count: int
    total_count: int
    submitted_at: str

    @property
    def score(self) -> float:
        return self.correct_count / self.total_count


@dataclass(frozen=True)
class QuizAttemptPage:
    items: Tuple[QuizAttemptSummary, ...]
    next_cursor: Optional[str]  # 다음 페이지가 있을 때만 마지막 항목의 attempt_id


# ── 제출 요청 키와 저장 결과 (#110) ─────────────────────────────
REQUEST_KEY_MAX_LENGTH = 128


class SubmitOutcome(str, Enum):
    """제출 저장의 결과. 저장소는 예외 대신 이 값을 돌려주고, 로그에는 답안·정답 본문을 남기지 않는다."""
    SAVED = 'saved'                        # 새 요청이라 시도를 저장했다.
    REUSED_EXISTING = 'reused_existing'    # 같은 키·같은 입력의 완료 기록이 있어 기존 결과를 돌려준다.
    REQUEST_CONFLICT = 'request_conflict'  # 같은 키인데 입력이 다르다. 아무것도 저장하지 않는다.
    QUIZ_CHANGED = 'quiz_changed'          # 완료 기록이 없고, 제출한 quiz_id가 현재 퀴즈와 다르다.


@dataclass(frozen=True)
class SubmitResult:
    outcome: SubmitOutcome
    attempt: Optional['QuizAttempt'] = None  # SAVED·REUSED_EXISTING일 때만 있다.


def validate_request_key(request_key: str) -> str:
    if not isinstance(request_key, str) or not request_key.strip():
        raise AttemptValidationError('request_key가 비어 있습니다.')
    if len(request_key) > REQUEST_KEY_MAX_LENGTH:
        raise AttemptValidationError(f'request_key는 {REQUEST_KEY_MAX_LENGTH}자 이하여야 합니다.')
    if not request_key.isprintable():
        raise AttemptValidationError('request_key에 제어 문자를 쓸 수 없습니다.')
    return request_key


def submit_input_hash(context_id: str, step: str, quiz_id: str,
                      answers: Mapping[int, Union[int, str]]) -> str:
    """같은 제출인지 판정하는 해시. 문항 순서와 직렬화 형식만 통일하고, 답안 문자열은 바꾸지 않는다.

    채점용 normalize_answer()를 쓰지 않으므로 공백·대소문자·유니코드 차이도 다른 입력으로 본다.
    """
    items = [[int(position), type(value).__name__, value] for position, value in sorted(answers.items())]
    payload = json.dumps({'v': 1, 'context_id': context_id, 'step': step, 'quiz_id': quiz_id,
                          'answers': items}, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
