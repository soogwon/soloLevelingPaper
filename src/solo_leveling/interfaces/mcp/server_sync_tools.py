"""논문 서버 동기화를 명시적 확인 카드와 상태 조회로 노출한다."""

from dataclasses import asdict
from importlib.resources import files
from secrets import token_urlsafe
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

import anyio
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from solo_leveling.application.server_sync.paper_metadata import PaperSyncPreview, SyncPreviewChanged
from solo_leveling.infrastructure.storage.server_client import ServerConnectionRequired


SERVER_SYNC_APP_URI = "ui://solo-leveling/server-sync.html"
SERVER_SYNC_NOTICE_VERSION = "paper-learning-sync-v2"
SERVER_SYNC_NOTICE = """로컬 설치 식별자와 선택한 논문의 ID, 제목, 원래 등록 시각을 연결된 Solo Leveling Paper 서버로 전송합니다.
이 논문의 학습 맥락 ID, 원문 버전 ID, 파일명, 마지막 학습 위치·시각과 완료한 학습 단계도 함께 전송합니다.
저자와 출판 연도는 현재 로컬에 없어 빈 값으로 전송합니다.
PDF 파일, 추출 원문, 번역문, 메모, 하이라이트, 질문·답변과 로컬 파일 경로는 전송하지 않습니다.
메타데이터는 연결된 계정에 저장됩니다.
취소하거나 동의하지 않으면 서버 전송 작업을 만들지 않습니다."""


class ServerSyncService(Protocol):
    def preview(self, paper_id: str) -> PaperSyncPreview: ...
    def grant_and_queue(self, paper_id: str, notice_version: str, operation_id: str, preview: PaperSyncPreview): ...
    def sync(self, operation_id: str): ...
    def status(self, operation_id: str): ...


class ServerSyncGate:
    def __init__(self, *, clock=monotonic):
        self.pending: dict[str, tuple[PaperSyncPreview, str, float]] = {}
        self.clock = clock

    def prepare(self, preview: PaperSyncPreview, operation_id: str) -> str:
        now = self.clock()
        self.pending = {key: value for key, value in self.pending.items() if value[2] > now}
        if len(self.pending) >= 128:
            raise ToolError("SERVER_SYNC_LIMIT: 잠시 후 동기화 안내를 다시 요청하세요.")
        token = token_urlsafe(32)
        self.pending[token] = (preview, operation_id, now + 600)
        return token

    def consume(self, token: str, paper_id: str, operation_id: str) -> PaperSyncPreview:
        pending = self.pending.get(token)
        if pending is None or pending[2] <= self.clock() or (pending[0].paper_id, pending[1]) != (paper_id, operation_id):
            raise ToolError("SERVER_SYNC_CONFIRMATION_REQUIRED: 동기화 안내 카드를 다시 열어 확인하세요.")
        del self.pending[token]
        return pending[0]

    def cancel(self, token: str) -> None:
        self.pending.pop(token, None)


def _output(result) -> dict[str, Any]:
    return asdict(result)


def register_server_sync(server, service: ServerSyncService) -> None:
    gate = ServerSyncGate()

    @server.resource(
        SERVER_SYNC_APP_URI, name="server-sync", title="논문 및 학습 기록 서버 동기화 확인",
        mime_type="text/html;profile=mcp-app",
        meta={"ui": {"csp": {"connectDomains": [], "resourceDomains": []}}},
    )
    def server_sync_html() -> str:
        return files("solo_leveling.interfaces.mcp").joinpath("assets/server-sync.html").read_text(encoding="utf-8")

    @server.tool(
        name="prepare_paper_server_sync", structured_output=True,
        meta={"ui": {"resourceUri": SERVER_SYNC_APP_URI}},
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=False, openWorldHint=False),
    )
    async def prepare_paper_server_sync(paper_id: str) -> CallToolResult:
        """선택한 논문의 서버 전송 범위를 표시합니다. 아직 동의나 전송 작업을 만들지 않습니다."""
        try:
            preview = await anyio.to_thread.run_sync(service.preview, paper_id)
        except KeyError:
            raise ToolError("NOT_FOUND: 요청한 논문을 찾을 수 없습니다.") from None
        except ServerConnectionRequired:
            raise ToolError("SERVER_CONNECTION_REQUIRED: 먼저 전용 연결 명령으로 서버 계정을 연결하세요.") from None
        operation_id = str(uuid4())
        notice = (f"{SERVER_SYNC_NOTICE}\n학습 기록 {preview.learning_record_count}건 · "
                  f"마지막 학습 시각 {preview.last_studied_at or '기록 없음'}")
        data = {"status": "confirmation_required", "notice": notice,
                "operation_id": operation_id, "paper_id": preview.paper_id, "title": preview.title,
                "learning_record_count": preview.learning_record_count,
                "last_studied_at": preview.last_studied_at}
        return CallToolResult(
            content=[TextContent(type="text", text=notice)], structuredContent=data,
            _meta={"confirmation_token": gate.prepare(preview, operation_id)},
        )

    @server.tool(
        name="sync_paper_to_server", structured_output=True,
        meta={"ui": {"visibility": ["app"]}},
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=False, openWorldHint=True),
    )
    async def sync_paper_to_server(paper_id: str, operation_id: str,
                                   confirmation_token: str) -> dict[str, Any]:
        """확인 카드에서 승인한 논문의 메타데이터와 학습 기록을 서버로 전송합니다."""
        preview = gate.consume(confirmation_token, paper_id, operation_id)
        try:
            queued = await anyio.to_thread.run_sync(
                service.grant_and_queue, paper_id, SERVER_SYNC_NOTICE_VERSION, operation_id, preview)
            return _output(await anyio.to_thread.run_sync(service.sync, queued.operation_id))
        except KeyError:
            raise ToolError("NOT_FOUND: 요청한 논문을 찾을 수 없습니다.") from None
        except ServerConnectionRequired:
            raise ToolError("SERVER_CONNECTION_REQUIRED: 먼저 전용 연결 명령으로 서버 계정을 연결하세요.") from None
        except SyncPreviewChanged:
            raise ToolError("SERVER_SYNC_PREVIEW_CHANGED: 논문 또는 계정이 변경되었습니다. 안내 카드를 다시 열어 주세요.") from None
        except Exception:
            raise ToolError("INTERNAL_ERROR: 서버 동기화를 시작하지 못했습니다.") from None

    @server.tool(
        name="cancel_paper_server_sync", structured_output=True,
        meta={"ui": {"visibility": ["app"]}},
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False),
    )
    async def cancel_paper_server_sync(confirmation_token: str) -> dict[str, str]:
        """확인 요청을 취소하며 동기화 작업을 만들지 않습니다."""
        gate.cancel(confirmation_token)
        return {"status": "cancelled"}

    @server.tool(
        name="get_paper_server_sync", structured_output=True,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False),
    )
    async def get_paper_server_sync(operation_id: str) -> dict[str, Any]:
        """기존 논문 서버 동기화 작업의 로컬 상태만 조회하며 재전송하지 않습니다."""
        try:
            return _output(await anyio.to_thread.run_sync(service.status, operation_id))
        except KeyError:
            raise ToolError("NOT_FOUND: 요청한 동기화 작업을 찾을 수 없습니다.") from None
