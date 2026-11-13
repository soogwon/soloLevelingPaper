"""저장된 퀴즈 조회와 요청 키 기반 제출을 MCP로 연결한다."""

from hashlib import sha256
from importlib.resources import files
from pathlib import Path

import anyio
from filelock import FileLock
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.application.evidence_qa.ports import GenerationUnavailable
from solo_leveling.application.evidence_qa.response_parser import GenerationFormatError
from solo_leveling.application.quiz.attempts import QuizAttemptService
from solo_leveling.application.quiz.service import NoSupportedQuiz, QuizService
from solo_leveling.domain.quiz import QuizValidationError
from solo_leveling.domain.quiz_attempt import AttemptValidationError, SubmitOutcome
from solo_leveling.infrastructure.database import repository
from .errors import to_tool_error
from .quiz_contracts import (
    AttemptOutput, ChoiceAnswer, ContractError, HistoryOutput, PublicQuiz, QuizContractAdapter, Step,
    ReviewOutput, ScoresOutput, SubmissionResult, SubmitOutput, Text, TextAnswer,
)


class StoredQuizService:
    def __init__(self, db_path: str, generator: QuizService | None = None):
        self.db_path = db_path
        self.attempts = QuizAttemptService(db_path)
        self.generator = generator

    def get_quiz(self, context_id, step):
        quiz = repository.get_quiz(self.db_path, context_id, step)
        if quiz is None:
            if repository.get_learning_context(self.db_path, context_id) is None:
                raise ContractError('NOT_FOUND')
            raise ContractError('QUIZ_NOT_READY')
        return quiz

    def generate_quiz(self, context_id, step):
        # 응답을 잃고 같은 요청을 다시 보내도 기존 퀴즈를 교체하거나 외부 호출을 반복하지 않는다.
        existing = repository.get_quiz(self.db_path, context_id, step)
        if existing is not None:
            return existing
        if repository.get_learning_context(self.db_path, context_id) is None:
            raise ContractError('NOT_FOUND')
        if self.generator is None:
            raise ContractError('QUIZ_NOT_READY')
        # 같은 DB를 쓰는 서버끼리도 같은 맥락·단계의 외부 호출은 한 번만 시작한다.
        db = Path(self.db_path).resolve()
        directory = db.with_name(db.name + '.quiz-locks')
        directory.mkdir(exist_ok=True)
        key = sha256(f'{context_id}:{step}'.encode()).hexdigest()
        with FileLock(directory / f'{key}.lock'):
            existing = repository.get_quiz(self.db_path, context_id, step)
            if existing is not None:
                return existing
            return self._generate(context_id, step)

    def _generate(self, context_id, step):
        try:
            return self.generator.generate(context_id, step).quiz
        except ResourceNotFoundError:
            raise ContractError('NOT_FOUND') from None
        except (NoSupportedQuiz, QuizValidationError):
            raise ContractError('QUIZ_NOT_READY') from None
        except (GenerationUnavailable, GenerationFormatError):
            raise ContractError('UPSTREAM_UNAVAILABLE') from None

    def submit(self, *, context_id, step, quiz_id, request_key, answers):
        try:
            result = self.attempts.submit_once(context_id=context_id, step=step, quiz_id=quiz_id,
                                               request_key=request_key, answers=answers)
        except AttemptValidationError:
            raise ContractError('INVALID_ARGUMENT') from None
        except ResourceNotFoundError:
            raise ContractError('NOT_FOUND') from None
        if result.outcome in (SubmitOutcome.REQUEST_CONFLICT, SubmitOutcome.QUIZ_CHANGED):
            raise ContractError(result.outcome.name)
        if result.outcome not in (SubmitOutcome.SAVED, SubmitOutcome.REUSED_EXISTING) or result.attempt is None:
            raise ContractError('INTERNAL_ERROR')
        return SubmissionResult(result.attempt, result.outcome is SubmitOutcome.REUSED_EXISTING)

    def get_attempt(self, *, context_id, attempt_id):
        try:
            attempt = self.attempts.get_attempt(attempt_id)
        except ResourceNotFoundError:
            raise ContractError('NOT_FOUND') from None
        if attempt.context_id != context_id:
            raise ContractError('NOT_FOUND')
        return attempt

    def history(self, context_id, step):
        try:
            return self.attempts.history(context_id, step)
        except ResourceNotFoundError:
            raise ContractError('NOT_FOUND') from None

    def history_page(self, context_id, step, limit, cursor):
        try:
            return self.attempts.history_page(context_id, step, limit, cursor)
        except AttemptValidationError:
            raise ContractError('INVALID_ARGUMENT') from None
        except ResourceNotFoundError:
            raise ContractError('NOT_FOUND') from None

    def scores(self, context_id):
        try:
            return self.attempts.step_scores(context_id)
        except ResourceNotFoundError:
            raise ContractError('NOT_FOUND') from None

    def review(self, context_id, attempt_id):
        # 복습 서비스는 시도 ID만 받으므로 원문 근거를 읽기 전에 맥락 소속을 검사한다.
        self.get_attempt(context_id=context_id, attempt_id=attempt_id)
        return self.attempts.review(attempt_id)


def register_quiz(server: FastMCP, adapter: QuizContractAdapter):
    uri = 'ui://solo-leveling/quiz.html'
    card = {'ui': {'resourceUri': uri}}

    @server.resource(uri, name='quiz', title='논문 학습 퀴즈',
                     mime_type='text/html;profile=mcp-app',
                     meta={'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}}})
    def quiz_html() -> str:
        return files('solo_leveling.interfaces.mcp').joinpath('assets/quiz.html').read_text(encoding='utf-8')

    async def invoke(method, payload):
        try:
            return await anyio.to_thread.run_sync(method, payload)
        except ContractError as error:
            messages = {
                'INVALID_ARGUMENT': 'INVALID_ARGUMENT: INVALID_INPUT: 입력값을 확인해주세요.',
                'NOT_FOUND': 'NOT_FOUND: 요청한 자료를 찾을 수 없습니다.',
                'QUIZ_NOT_READY': 'QUIZ_NOT_READY: 아직 생성된 퀴즈가 없습니다.',
                'QUIZ_CHANGED': 'QUIZ_CHANGED: 조회한 퀴즈가 변경되었습니다.',
                'REQUEST_CONFLICT': 'REQUEST_CONFLICT: request_key가 이전 요청과 일치하지 않습니다.',
                'UPSTREAM_UNAVAILABLE': 'UPSTREAM_UNAVAILABLE: 퀴즈 생성 서비스를 사용할 수 없습니다.',
                'INTERNAL_ERROR': 'INTERNAL_ERROR: 요청을 처리하지 못했습니다.',
            }
            raise ToolError(messages[error.code]) from None
        except Exception as error:
            raise to_tool_error(error) from None

    @server.tool(structured_output=True, annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def get_quiz_scores(context_id: Text) -> ScoresOutput:
        """학습 단계별 시도 수와 최근·최고 점수(0~1)를 조회합니다. 미응시 점수는 null입니다."""
        return await invoke(adapter.scores, dict(context_id=context_id))

    @server.tool(structured_output=True, annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def review_quiz_attempt(context_id: Text, attempt_id: Text) -> ReviewOutput:
        """같은 학습 맥락에서 해당 시도의 오답·정답과 연결된 원문 근거를 조회합니다."""
        return await invoke(adapter.review, dict(context_id=context_id, attempt_id=attempt_id))

    @server.tool(structured_output=True, meta=card, annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def get_quiz_attempt(context_id: Text, attempt_id: Text) -> AttemptOutput:
        """같은 학습 맥락에서 제출 당시 문항·답안·채점 결과와 근거 ID를 조회합니다."""
        return await invoke(adapter.get_attempt, dict(context_id=context_id, attempt_id=attempt_id))

    @server.tool(structured_output=True, annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def list_quiz_attempts(context_id: Text, step: Step | None = None,
                                 limit: int = 20, cursor: Text | None = None) -> HistoryOutput:
        """퀴즈 시도 요약을 최신순으로 조회합니다. 다음 페이지는 같은 맥락·단계와 next_cursor로 요청하세요."""
        return await invoke(adapter.list_attempts, dict(context_id=context_id, step=step,
                                                       limit=limit, cursor=cursor))

    @server.tool(structured_output=True, meta=card, annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def get_quiz(context_id: Text, step: Step) -> PublicQuiz:
        """저장된 단계별 공개 퀴즈를 조회합니다. QUIZ_NOT_READY이면 전송 내용을 안내한 뒤 generate_quiz로 생성하세요."""
        try:
            return await invoke(adapter.get_quiz, dict(context_id=context_id, step=step))
        except ToolError as error:
            if str(error).partition(':')[0] == 'QUIZ_NOT_READY':
                raise ToolError('QUIZ_NOT_READY: 전송 내용을 안내한 뒤 generate_quiz로 생성하세요.') from None
            raise

    @server.tool(structured_output=True, meta=card, annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
    async def generate_quiz(context_id: Text, step: Step) -> PublicQuiz:
        """해당 단계의 퀴즈가 없으면 생성하고 저장하며, 이미 있으면 외부 호출 없이 재사용합니다.

        외부 API 호출을 허용한 경우 단계에 배정된 번역문과 원문 근거를 OpenAI로 보내며
        비용이 발생할 수 있습니다. 호출 전에 사용자에게 전송 내용을 안내하세요.
        UPSTREAM_UNAVAILABLE이면 자동 반복 생성하지 말고 외부 생성 설정을 확인하세요.
        """
        return await invoke(adapter.generate_quiz, dict(context_id=context_id, step=step))

    @server.tool(structured_output=True, meta=card, annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def submit_quiz(context_id: Text, step: Step, quiz_id: Text, request_key: Text,
                          answers: list[ChoiceAnswer | TextAnswer]) -> SubmitOutput:
        """조회한 quiz_id에 사용자의 답안을 제출합니다. 문항·선택지 번호는 0부터 시작합니다.

        무응답 문항은 생략하세요. 단답은 정규화 후 완전 일치로 채점합니다.
        한 번에 최대 64문항, 단답당 5000자, 전체 제출 입력은 UTF-8 JSON 기준 64 KiB까지입니다.
        응답 유실 후 재전송은 같은 키와 원래 입력을 사용하세요. 새 키는 의도적인 재응시에만 사용하세요.
        REQUEST_CONFLICT 또는 QUIZ_CHANGED이면 자동 재제출하지 마세요.
        """
        return await invoke(adapter.submit, dict(context_id=context_id, step=step, quiz_id=quiz_id,
                            request_key=request_key, answers=answers))
