"""웹에서 발급된 학습 이어하기 코드를 로컬 MCP에 연결한다."""

import re
from typing import Any, NotRequired, TypedDict, cast

import anyio
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from solo_leveling.application.server_sync.resume import (
    ResumeRequestExpired, ResumeRequestUnavailable,
)
from solo_leveling.infrastructure.storage.server_client import ServerConnectionRequired

_CODE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class ResumeToolResult(TypedDict):
    resume_request_id: str
    paper_id: str
    title: str | None
    version_id: str
    learning_record_id: str
    last_position: dict[str, Any] | None
    annotation_id: str | None
    annotation: dict[str, Any] | None
    file_name: str | None
    file_access_status: str
    resume_position_status: NotRequired[str]
    status: str
    reason: str | None


def register_resume(server, resolver) -> None:
    @server.tool(
        name="resolve_resume_request", structured_output=True,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=True),
    )
    async def resolve_resume_request(code: str) -> ResumeToolResult:
        """외부 웹에서 발급한 이어하기 코드를 같은 계정의 서버 기록으로 조회합니다.

        서버에서 논문·원문 버전·학습 기록·마지막 위치를 읽지만 읽기 상태를 바꾸거나
        학습 작업을 만들지 않습니다. 반환된 file_access_status를 확인한 뒤 현재 PC의
        PDF·검색 데이터·재개 위치 준비 여부를 응답 상태에 따라 안내하세요.
        """
        code = code.strip()
        if not _CODE.fullmatch(code):
            raise ToolError("INVALID_ARGUMENT: 이어하기 코드 형식을 확인해 주세요.")
        try:
            result = await anyio.to_thread.run_sync(resolver.resolve, code)
            return cast(ResumeToolResult, result)
        except ResumeRequestExpired:
            raise ToolError("RESUME_REQUEST_EXPIRED: 웹에서 이어하기 코드를 다시 만들어 주세요.") from None
        except ResumeRequestUnavailable:
            raise ToolError("NOT_FOUND: 이어하기 코드를 찾을 수 없습니다.") from None
        except ServerConnectionRequired:
            raise ToolError("SERVER_CONNECTION_REQUIRED: 먼저 서버 계정을 연결하세요.") from None
        except Exception:
            raise ToolError("INTERNAL_ERROR: 이어하기 정보를 확인하지 못했습니다.") from None
