"""저장된 퀴즈 조회와 요청 키 기반 제출을 MCP로 연결한다."""

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.application.quiz.attempts import QuizAttemptService
from solo_leveling.domain.quiz_attempt import AttemptValidationError, SubmitOutcome
from solo_leveling.infrastructure.database import repository
from .errors import to_tool_error
from .quiz_contracts import (
    ChoiceAnswer, ContractError, PublicQuiz, QuizContractAdapter, Step,
    SubmissionResult, SubmitOutput, Text, TextAnswer,
)


class StoredQuizService:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.attempts = QuizAttemptService(db_path)

    def get_quiz(self, context_id, step):
        quiz = repository.get_quiz(self.db_path, context_id, step)
        if quiz is None:
            if repository.get_learning_context(self.db_path, context_id) is None:
                raise ContractError('NOT_FOUND')
            raise ContractError('QUIZ_NOT_READY')
        return quiz

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


def register_quiz(server: FastMCP, adapter: QuizContractAdapter):
    async def invoke(method, payload):
        try:
            return await anyio.to_thread.run_sync(method, payload)
        except ContractError as error:
            raise ToolError(error.code) from None
        except Exception as error:
            raise to_tool_error(error) from None

    @server.tool(structured_output=True, annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def get_quiz(context_id: Text, step: Step) -> PublicQuiz:
        """저장된 단계별 공개 퀴즈를 조회합니다. QUIZ_NOT_READY이면 아직 퀴즈가 없습니다."""
        return await invoke(adapter.get_quiz, dict(context_id=context_id, step=step))

    @server.tool(structured_output=True, annotations=ToolAnnotations(
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
