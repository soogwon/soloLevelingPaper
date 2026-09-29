"""서비스 오류를 비밀 정보가 없는 MCP 오류 코드로 변환한다."""

from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.application.evidence_qa.answer import AnswerGenerationError
from solo_leveling.application.evidence_qa.errors import (
    DataIntegrityError, InvalidArgumentError, ResourceNotFoundError,
)
from solo_leveling.domain.context import ContextNotReadyError
from solo_leveling.domain.models import RequestConflictError


class UnsupportedDocumentError(ValueError):
    """MCP로 등록할 파일이 지원 형식·크기 조건을 만족하지 않는다."""


def to_tool_error(error: Exception) -> ToolError:
    """원래 예외 메시지·경로·스택을 MCP 응답에 포함하지 않는다."""
    if isinstance(error, InvalidArgumentError):
        return ToolError('INVALID_ARGUMENT: 입력값을 확인해주세요.')
    if isinstance(error, ResourceNotFoundError):
        return ToolError('NOT_FOUND: 요청한 자료를 찾을 수 없습니다.')
    if isinstance(error, RequestConflictError):
        return ToolError('CONFLICT: request_key가 이전 요청과 일치하지 않습니다.')
    if isinstance(error, UnsupportedDocumentError):
        return ToolError('UNSUPPORTED_DOCUMENT: 지원하는 PDF 파일인지 확인해주세요.')
    if isinstance(error, ContextNotReadyError):
        status = error.status.value if error.status is not None else 'unknown'
        job = error.job_id or 'unknown'
        return ToolError(f'PAPER_NOT_READY: 등록 작업 상태를 확인해주세요. job_id={job}, status={status}')
    if isinstance(error, AnswerGenerationError):
        return ToolError('UPSTREAM_UNAVAILABLE: 답변 생성 서비스를 사용할 수 없습니다.')
    if isinstance(error, DataIntegrityError):
        return ToolError('INTERNAL_ERROR: 저장된 데이터의 연결을 확인하지 못했습니다.')
    return ToolError('INTERNAL_ERROR: 요청을 처리하지 못했습니다.')
