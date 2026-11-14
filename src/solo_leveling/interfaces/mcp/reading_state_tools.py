"""사용자의 명시적 요청으로만 읽기 완료와 완료 취소를 기록한다."""
import anyio
from functools import partial
from mcp.types import ToolAnnotations
from solo_leveling.application.reading_state import ReadingState, ReadingStateConflict
from .errors import to_tool_error, tool_error


def register_reading_state(server, service):
    async def invoke(method, *args):
        try:
            return await anyio.to_thread.run_sync(partial(method, *args))
        except ReadingStateConflict:
            raise tool_error('CONFLICT', '읽기 상태를 다시 조회하세요.', reason='READING_STATE_CONFLICT') from None
        except Exception as error:
            raise to_tool_error(error) from None

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def get_reading_state(paper_id: str) -> ReadingState:
        """논문의 명시적 읽기 상태와 revision을 조회합니다. 단계 진행률과 별개입니다."""
        return await invoke(service.get, paper_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                            idempotentHint=False, openWorldHint=False))
    async def set_reading_completed(paper_id: str, completed: bool, expected_revision: int) -> ReadingState:
        """사용자가 명시적으로 요청했을 때만 완료(true) 또는 완료 취소(false→reading)를 저장합니다.
        조회한 revision을 전달하세요. 단계 완료나 퀴즈 결과로 자동 호출하지 마세요.
        응답 유실·충돌 시 get_reading_state로 확인하고 자동으로 revision을 바꿔 재시도하지 마세요.
        """
        return await invoke(service.set_completed, paper_id, completed, expected_revision)
