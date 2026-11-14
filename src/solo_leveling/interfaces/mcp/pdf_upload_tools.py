"""PDF 서버 업로드를 별도 동의 카드와 연결한다."""

from dataclasses import asdict
from importlib.resources import files
from secrets import token_urlsafe
from time import monotonic
from typing import Protocol

import anyio
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from solo_leveling.application.server_sync.pdf_upload import PdfUploadPreview, PdfUploadStageError
from solo_leveling.infrastructure.storage.server_client import ServerConnectionRequired, ServerResponseInvalid


PDF_UPLOAD_APP_URI = "ui://solo-leveling/pdf-upload.html"
PDF_UPLOAD_NOTICE = """선택한 원문 PDF 파일 전체를 연결된 Solo Leveling Paper 서버의 비공개 저장소로 전송합니다.
서버에는 연결 계정, 논문·원문 버전, 파일명, 크기, SHA-256 검증값과 저장 상태가 기록됩니다.
업로드된 PDF는 같은 계정으로 로그인한 다른 PC의 외부 웹에서 임시 주소로 열 수 있습니다.
PDF 원문 전체가 전송되며 저장 공간을 사용합니다. 메타데이터 동기화 중지와 이미 올린 PDF 삭제는 별도 동작입니다.
취소하거나 동의하지 않으면 PDF를 전송하지 않습니다."""


class PdfUploadService(Protocol):
    def preview(self, paper_id: str, version_id: str | None = None) -> PdfUploadPreview: ...
    def upload(self, expected: PdfUploadPreview): ...
    def status(self, expected: PdfUploadPreview) -> dict: ...


class PdfUploadGate:
    def __init__(self, *, clock=monotonic):
        self.pending: dict[str, tuple[PdfUploadPreview, float]] = {}
        self.confirmed: dict[str, tuple[PdfUploadPreview, float]] = {}
        self.clock = clock

    def prepare(self, preview: PdfUploadPreview) -> str:
        now = self.clock()
        self.pending = {key: value for key, value in self.pending.items() if value[1] > now}
        self.confirmed = {key: value for key, value in self.confirmed.items() if value[1] > now}
        if len(self.pending) >= 128:
            raise ToolError("PDF_UPLOAD_LIMIT: 잠시 후 업로드 안내를 다시 요청하세요.")
        token = token_urlsafe(32)
        self.pending[token] = (preview, now + 600)
        return token

    def consume(self, token: str, paper_id: str, version_id: str) -> PdfUploadPreview:
        pending = self.pending.get(token)
        if (pending is None or pending[1] <= self.clock()
                or (pending[0].paper_id, pending[0].version_id) != (paper_id, version_id)):
            raise ToolError("PDF_UPLOAD_CONFIRMATION_REQUIRED: PDF 업로드 안내 카드를 다시 확인하세요.")
        del self.pending[token]
        self.confirmed[token] = pending
        return pending[0]

    def lookup_confirmed(self, token: str, paper_id: str, version_id: str) -> PdfUploadPreview:
        confirmed = self.confirmed.get(token)
        if (confirmed is None or confirmed[1] <= self.clock()
                or (confirmed[0].paper_id, confirmed[0].version_id) != (paper_id, version_id)):
            raise ToolError("PDF_UPLOAD_CONFIRMATION_REQUIRED: PDF 업로드 안내 카드를 다시 확인하세요.")
        return confirmed[0]

    def cancel(self, token: str) -> None:
        self.pending.pop(token, None)
        self.confirmed.pop(token, None)


def _pdf_upload_stage_tool_error(exc: PdfUploadStageError) -> ToolError:
    code, message = {
        "prepare": ("PDF_UPLOAD_PREPARE_FAILED", "업로드 준비 요청에 실패했습니다."),
        "transfer": ("PDF_UPLOAD_TRANSFER_FAILED", "PDF 파일 전송에 실패했습니다."),
        "complete": ("PDF_UPLOAD_COMPLETE_FAILED", "업로드 완료 확인에 실패했습니다."),
        "status": ("PDF_UPLOAD_STATUS_FAILED", "업로드 상태 확인에 실패했습니다."),
    }.get(exc.stage, ("PDF_UPLOAD_FAILED", "PDF 업로드를 완료하지 못했습니다."))
    details = []
    if exc.status_code is not None:
        details.append(f"HTTP {exc.status_code}")
    if exc.detail_code:
        details.append(exc.detail_code)
    suffix = f" ({', '.join(details)})" if details else ""
    return ToolError(f"{code}: {message}{suffix} 자동으로 다시 전송하지 않습니다.")


def _pdf_upload_business_result(error: ToolError) -> CallToolResult:
    """호스트의 오류 변환을 거치지 않고 카드에 안전한 업무 상태만 전달한다."""
    code = str(error).partition(":")[0]
    allowed_codes = {
        "PDF_ALREADY_UPLOADED", "PDF_UPLOAD_PREPARE_FAILED",
        "PDF_UPLOAD_TRANSFER_FAILED", "PDF_UPLOAD_COMPLETE_FAILED",
        "PDF_UPLOAD_STATUS_FAILED",
        "PDF_UPLOAD_FAILED", "SERVER_CONNECTION_REQUIRED",
        "PDF_UPLOAD_PREVIEW_CHANGED", "PDF_UPLOAD_CONFIRMATION_REQUIRED",
    }
    if code not in allowed_codes:
        code = "PDF_UPLOAD_FAILED"
    return CallToolResult(
        content=[TextContent(type="text", text="PDF 업로드를 완료하지 않았습니다.")],
        structuredContent={
            "status": "already_uploaded" if code == "PDF_ALREADY_UPLOADED" else "failed",
            "error_code": code,
        },
    )


def register_pdf_upload(server, service: PdfUploadService) -> None:
    gate = PdfUploadGate()

    @server.resource(
        PDF_UPLOAD_APP_URI, name="pdf-upload", title="PDF 서버 업로드 확인",
        mime_type="text/html;profile=mcp-app",
        meta={"ui": {"csp": {"connectDomains": [], "resourceDomains": []}}},
    )
    def pdf_upload_html() -> str:
        return files("solo_leveling.interfaces.mcp").joinpath(
            "assets/pdf-upload.html").read_text(encoding="utf-8")

    @server.tool(
        name="prepare_paper_pdf_upload", structured_output=True,
        meta={"ui": {"resourceUri": PDF_UPLOAD_APP_URI}},
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=False, openWorldHint=False),
    )
    async def prepare_paper_pdf_upload(
        paper_id: str, version_id: str | None = None,
    ) -> CallToolResult:
        """서버에 올릴 관리 PDF와 전송 범위를 표시합니다. 아직 파일을 전송하지 않습니다."""
        try:
            preview = await anyio.to_thread.run_sync(service.preview, paper_id, version_id)
        except KeyError:
            raise ToolError("NOT_FOUND: 업로드할 논문 원문 버전을 찾을 수 없습니다.") from None
        except ServerConnectionRequired as exc:
            code = "PAPER_SYNC_REQUIRED" if str(exc) == "paper_sync_required" else "SERVER_CONNECTION_REQUIRED"
            raise ToolError(f"{code}: 먼저 논문 기록을 같은 서버 계정에 동기화하세요.") from None
        except (OSError, ValueError):
            raise ToolError("PDF_UNAVAILABLE: 관리 PDF 파일을 안전하게 확인할 수 없습니다.") from None
        notice = (f"{PDF_UPLOAD_NOTICE}\n파일명 {preview.file_name} · 크기 {preview.file_size}바이트 · "
                  f"SHA-256 {preview.sha256_hex}")
        confirmation_token = gate.prepare(preview)
        data = {
            "status": "confirmation_required", "notice": notice,
            "paper_id": preview.paper_id, "server_paper_id": preview.server_paper_id,
            "version_id": preview.version_id, "file_name": preview.file_name,
            "file_size": preview.file_size, "sha256": preview.sha256_hex,
            "confirmation_token": confirmation_token,
        }
        return CallToolResult(
            content=[TextContent(type="text", text=notice)], structuredContent=data,
            _meta={"confirmation_token": confirmation_token},
        )

    @server.tool(
        name="upload_paper_pdf_to_server", structured_output=True,
        meta={"ui": {"visibility": ["app"]}},
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=False, openWorldHint=True),
    )
    async def upload_paper_pdf_to_server(
        paper_id: str, version_id: str, confirmation_token: str,
    ) -> CallToolResult:
        """카드에서 승인한 PDF 파일을 비공개 서버 저장소에 업로드하고 검증합니다."""
        try:
            preview = gate.consume(confirmation_token, paper_id, version_id)
            data = asdict(await anyio.to_thread.run_sync(service.upload, preview))
            return CallToolResult(
                content=[TextContent(type="text", text="PDF 업로드를 완료했습니다.")],
                structuredContent=data,
            )
        except FileExistsError:
            error = ToolError("PDF_ALREADY_UPLOADED: 이 원문 버전의 PDF가 이미 준비되어 있습니다.")
        except PdfUploadStageError as exc:
            error = _pdf_upload_stage_tool_error(exc)
        except ServerResponseInvalid:
            error = ToolError("PDF_UPLOAD_FAILED: 업로드 응답을 확인할 수 없습니다.")
        except ServerConnectionRequired:
            error = ToolError("SERVER_CONNECTION_REQUIRED: 서버 계정을 다시 연결하세요.")
        except ValueError as exc:
            if str(exc) == "PDF_UPLOAD_PREVIEW_CHANGED":
                error = ToolError(
                    "PDF_UPLOAD_PREVIEW_CHANGED: 파일이 변경되었습니다. 안내 카드를 다시 확인하세요.")
            else:
                error = ToolError("PDF_UPLOAD_FAILED: 서버가 업로드 응답을 확인하지 못했습니다.")
        except ToolError as exc:
            error = exc
        except Exception:
            error = ToolError("PDF_UPLOAD_FAILED: PDF 업로드를 완료하지 못했습니다.")
        return _pdf_upload_business_result(error)

    @server.tool(
        name="check_paper_pdf_upload_status", structured_output=True,
        meta={"ui": {"visibility": ["app"]}},
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=True),
    )
    async def check_paper_pdf_upload_status(
        paper_id: str, version_id: str, confirmation_token: str,
    ) -> CallToolResult:
        """응답이 불확실한 업로드의 서버 상태를 원문 재전송 없이 확인합니다."""
        try:
            preview = gate.lookup_confirmed(confirmation_token, paper_id, version_id)
            data = await anyio.to_thread.run_sync(service.status, preview)
            return CallToolResult(
                content=[TextContent(type="text", text="PDF 업로드 상태를 확인했습니다.")],
                structuredContent=data,
            )
        except PdfUploadStageError as exc:
            error = _pdf_upload_stage_tool_error(exc)
        except ServerConnectionRequired:
            error = ToolError("SERVER_CONNECTION_REQUIRED: 서버 계정을 다시 연결하세요.")
        except ValueError:
            error = ToolError("PDF_UPLOAD_PREVIEW_CHANGED: 파일이 변경되었습니다.")
        except ToolError as exc:
            error = exc
        except Exception:
            error = ToolError("PDF_UPLOAD_FAILED: 업로드 상태를 확인하지 못했습니다.")
        return _pdf_upload_business_result(error)

    @server.tool(
        name="cancel_paper_pdf_upload", structured_output=True,
        meta={"ui": {"visibility": ["app"]}},
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False),
    )
    async def cancel_paper_pdf_upload(confirmation_token: str) -> dict[str, str]:
        gate.cancel(confirmation_token)
        return {"status": "cancelled"}
