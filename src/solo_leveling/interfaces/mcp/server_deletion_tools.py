"""서버 삭제는 별도 확인 카드에서만 접수하며 로컬 자료는 보존한다."""

from importlib.resources import files
from typing import Literal
import anyio
from pydantic import BaseModel
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from mcp.server.fastmcp.exceptions import ToolError
from solo_leveling.domain.deletion_policy import DELETION_NOTICE_KO
from .confirmations import ConfirmationStore, ConfirmationLimitExceeded

URI = "ui://solo-leveling/server-deletion.html"


class DeletionStatusOutput(BaseModel):
    paper_id: str
    status: Literal["blocking", "cleaning", "failed", "complete"]
    backup_erasure_confirmed: Literal[False]
    local_data_preserved: Literal[True]


def register_server_deletion(server, service, *, confirmations=None):
    store = confirmations if confirmations is not None else ConfirmationStore()

    async def invoke(name, *args):
        try:
            return await anyio.to_thread.run_sync(getattr(service, name), *args)
        except KeyError:
            raise ToolError("DELETION_NOT_FOUND: 현재 연결의 대상 또는 요청을 찾을 수 없습니다.") from None
        except Exception:
            raise ToolError("DELETION_UNAVAILABLE: 결과를 확인하지 못했습니다. 기존 요청을 조회하세요.") from None

    @server.resource(
        URI,
        name="server-deletion",
        mime_type="text/html;profile=mcp-app",
        meta={"ui": {"csp": {"connectDomains": [], "resourceDomains": []}}},
    )
    def html():
        return files("solo_leveling.interfaces.mcp").joinpath("assets/server-deletion.html").read_text(encoding="utf-8")

    @server.tool(
        name="prepare_server_paper_deletion",
        structured_output=True,
        meta={"ui": {"resourceUri": URI}},
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=False
        ),
    )
    async def prepare(paper_id: str) -> CallToolResult:
        """서버 논문과 관련 기록 삭제 범위를 안내한다. 아직 접수하지 않는다. 로컬 삭제·동의 철회와 다르다."""
        preview = await invoke("preview", paper_id)
        try:
            token = store.issue("server_deletion", [paper_id], preview)
        except ConfirmationLimitExceeded:
            raise ToolError("DELETION_CONFIRMATION_LIMIT: 잠시 후 삭제 안내를 다시 요청하세요.") from None
        notice = (
            DELETION_NOTICE_KO
            + " 서버 논문과 관련 기록을 삭제합니다. 복구를 보장하지 않습니다. 이 기기의 로컬 자료는 보존됩니다."
        )
        return CallToolResult(
            content=[TextContent(type="text", text=notice)],
            structuredContent={
                "paper_id": paper_id,
                "title": preview["title"],
                "notice": notice,
                "confirmation_token": token,
            },
        )

    @server.tool(
        name="confirm_server_paper_deletion",
        structured_output=True,
        meta={"ui": {"visibility": ["app"]}},
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True),
    )
    async def confirm(paper_id: str, confirmation_token: str) -> DeletionStatusOutput:
        """사용자가 삭제 범위를 승인한 카드에서만 접수한다. 확인되지 않은 결과를 완료로 표시하지 않는다."""
        accepted, preview = store.take("server_deletion", confirmation_token, [paper_id])
        if not accepted:
            raise ToolError("DELETION_CONFIRMATION_REQUIRED: 삭제 안내를 다시 확인하세요.")
        return DeletionStatusOutput(**await invoke("confirm", preview))

    @server.tool(
        name="get_server_paper_deletion",
        structured_output=True,
        meta={"ui": {"resourceUri": URI}},
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    )
    async def status(paper_id: str) -> DeletionStatusOutput:
        """현재 연결의 기존 삭제 요청만 조회한다. 새 접수나 물리 삭제를 실행하지 않는다."""
        return DeletionStatusOutput(**await invoke("status", paper_id))

    @server.tool(
        name="retry_server_paper_deletion",
        structured_output=True,
        meta={"ui": {"visibility": ["app"]}},
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
    )
    async def retry(paper_id: str) -> DeletionStatusOutput:
        """기록된 삭제 의도를 동일 키로만 재전송한다. 이미 접수됐으면 조회만 한다."""
        return DeletionStatusOutput(**await invoke("retry", paper_id))
