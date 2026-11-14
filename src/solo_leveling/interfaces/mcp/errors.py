"""서비스 오류를 비밀 정보가 없는 MCP 오류 코드로 변환한다."""

from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.application.evidence_qa.answer import AnswerGenerationError
from solo_leveling.application.evidence_qa.errors import (
    DataIntegrityError, InvalidArgumentError, LocalPdfNotFoundError, ResourceNotFoundError,
)
from solo_leveling.domain.context import ContextNotReadyError
from solo_leveling.domain.models import RequestConflictError
from solo_leveling.infrastructure.embeddings.process_embedder import EmbeddingProcessError
from solo_leveling.infrastructure.storage.pdf_store import UnsupportedDocumentError
from .ingestion import IngestionShuttingDownError


INVALID_INPUT_MESSAGE = '입력값을 확인해주세요.'
NOT_FOUND_MESSAGE = '요청한 자료를 찾을 수 없습니다.'


def tool_error(code: str, message: str, *, reason: str | None = None, field: str | None = None) -> ToolError:
    """모든 MCP 오류를 ``CODE[: REASON][: field=이름]: 안내`` 한 가지 형식으로 만든다.

    field에는 공개 입력 스키마의 필드 이름만 넣는다. 사용자가 보낸 값은 넣지 않는다.
    """
    parts = [code, *([reason] if reason else []), *([f'field={field}'] if field else []), message]
    return ToolError(': '.join(parts))


def invalid_input(field: str | None = None, reason: str = 'INVALID_INPUT') -> ToolError:
    return tool_error('INVALID_ARGUMENT', INVALID_INPUT_MESSAGE, reason=reason, field=field)


def registration_input_error(field: str | None = None) -> ToolError:
    """등록 입력의 수정 방법만 안내하며 호출자가 보낸 값은 포함하지 않는다."""
    return tool_error('INVALID_ARGUMENT',
        'source.kind는 local_file, source.relative_path는 설정된 import 루트 기준 PDF 상대 경로입니다. '
        '루트 폴더명을 접두사로 중복하지 마세요. request_key는 공백만으로 이루어지지 않은 '
        '1~200자 문자열이며 동일 요청 재시도에 재사용합니다. 호출 예시: '
        '{"source":{"kind":"local_file","relative_path":"glove.pdf"},"request_key":"registration-01"}',
        reason='INVALID_INPUT', field=field)


def to_tool_error(error: Exception) -> ToolError:
    """원래 예외 메시지·경로·스택을 MCP 응답에 포함하지 않는다."""
    if isinstance(error, IngestionShuttingDownError):
        return ToolError('SERVER_SHUTTING_DOWN: 서버 종료 중에는 새 요청을 시작할 수 없습니다.')
    if isinstance(error, EmbeddingProcessError):
        return ToolError(f'UPSTREAM_UNAVAILABLE: {error.code.value}: 임베딩 처리를 완료하지 못했습니다.')
    if isinstance(error, LocalPdfNotFoundError):
        return ToolError(
            f'SOURCE_NOT_FOUND: PDF 파일을 찾을 수 없습니다. '
            f'{error.relative_path}에 파일을 복사한 뒤 다시 시도하세요.')
    if isinstance(error, InvalidArgumentError):
        return invalid_input()
    if isinstance(error, ResourceNotFoundError):
        return tool_error('NOT_FOUND', NOT_FOUND_MESSAGE)
    if isinstance(error, RequestConflictError):
        # 같은 request_key의 다른 요청은 퀴즈·재파싱·복구와 같은 REQUEST_CONFLICT로 알린다.
        return tool_error('REQUEST_CONFLICT', 'request_key가 이전 요청과 일치하지 않습니다.')
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
