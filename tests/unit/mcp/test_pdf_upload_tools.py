"""PDF 원문은 전용 카드의 명시적 승인 뒤에만 서버로 전송된다."""

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.application.server_sync.pdf_upload import (
    PdfUploadPreview, PdfUploadResult, PdfUploadStageError,
)
from solo_leveling.interfaces.mcp.pdf_upload_tools import (
    PDF_UPLOAD_APP_URI, _pdf_upload_error_result, _pdf_upload_stage_tool_error,
)
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from solo_leveling.infrastructure.storage.server_client import ServerConnectionRequired, ServerResponseInvalid


def make_server(service):
    return create_server(MCPServices(Mock(), Mock(), pdf_upload=service))


def preview():
    return PdfUploadPreview(
        "account", "paper-1", "00000000-0000-0000-0000-000000000201", "version-1",
        "paper.pdf", 1234, "ab" * 32, Path("paper.pdf"),
    )


def test_card_approval_uploads_exact_preview_once():
    service = Mock()
    service.preview.return_value = preview()
    service.upload.return_value = PdfUploadResult(
        "00000000-0000-0000-0000-000000000601", preview().server_paper_id,
        "version-1", "paper.pdf", 1234, "application/pdf", "ready")
    server = make_server(service)

    async def check():
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools["prepare_paper_pdf_upload"].meta["ui"]["resourceUri"] == PDF_UPLOAD_APP_URI
        assert tools["upload_paper_pdf_to_server"].meta["ui"]["visibility"] == ["app"]
        prepared = await server.call_tool("prepare_paper_pdf_upload", {"paper_id": "paper-1"})
        service.upload.assert_not_called()
        assert "PDF 원문 전체" in prepared.structuredContent["notice"]
        assert prepared.structuredContent["confirmation_token"] == prepared.meta["confirmation_token"]
        result = await server.call_tool("upload_paper_pdf_to_server", {
            "paper_id": "paper-1", "version_id": "version-1",
            "confirmation_token": prepared.meta["confirmation_token"],
        })
        assert result.structuredContent["status"] == "ready"
        repeated = await server.call_tool("upload_paper_pdf_to_server", {
            "paper_id": "paper-1", "version_id": "version-1",
            "confirmation_token": prepared.meta["confirmation_token"],
        })
        assert repeated.isError is True
        assert repeated.content[0].text.startswith("PDF_UPLOAD_CONFIRMATION_REQUIRED:")

    asyncio.run(check())
    service.preview.assert_called_once_with("paper-1", None)
    service.upload.assert_called_once_with(preview())


def test_pdf_upload_resource_is_bundled():
    server = make_server(Mock())

    async def check():
        content = await server.read_resource(PDF_UPLOAD_APP_URI)
        assert "동의하고 업로드" in content[0].content
        assert '<script src=' not in content[0].content

    asyncio.run(check())


@pytest.mark.parametrize(("stage", "expected"), [
    ("prepare", "PDF_UPLOAD_PREPARE_FAILED"),
    ("transfer", "PDF_UPLOAD_TRANSFER_FAILED"),
    ("complete", "PDF_UPLOAD_COMPLETE_FAILED"),
])
def test_upload_stage_failure_has_stable_tool_error(stage, expected):
    error = _pdf_upload_stage_tool_error(
        PdfUploadStageError(stage, 503, "storage_unavailable"))
    assert expected in str(error)
    assert "HTTP 503" in str(error)
    assert "storage_unavailable" in str(error)


def test_expected_upload_error_is_returned_as_mcp_error_result():
    result = _pdf_upload_error_result(ToolError(
        "PDF_ALREADY_UPLOADED: 이 원문 버전의 PDF가 이미 준비되어 있습니다."))

    assert result.isError is True
    assert result.structuredContent is None
    assert result.content[0].text.startswith("PDF_ALREADY_UPLOADED:")


@pytest.mark.parametrize(("failure", "code"), [
    (FileExistsError("private-detail"), "PDF_ALREADY_UPLOADED"),
    (PdfUploadStageError("prepare", 503, "storage_unavailable"), "PDF_UPLOAD_PREPARE_FAILED"),
    (PdfUploadStageError("transfer", 403), "PDF_UPLOAD_TRANSFER_FAILED"),
    (PdfUploadStageError("complete", 422), "PDF_UPLOAD_COMPLETE_FAILED"),
    (ServerResponseInvalid("private-detail"), "PDF_UPLOAD_FAILED"),
    (ServerConnectionRequired("private-detail"), "SERVER_CONNECTION_REQUIRED"),
    (ValueError("PDF_UPLOAD_PREVIEW_CHANGED"), "PDF_UPLOAD_PREVIEW_CHANGED"),
    (ValueError("private-detail"), "PDF_UPLOAD_FAILED"),
    (RuntimeError("private-detail"), "PDF_UPLOAD_FAILED"),
])
def test_upload_failure_preserves_code_and_consumes_token(failure, code):
    service = Mock()
    service.preview.return_value = preview()
    service.upload.side_effect = failure
    server = make_server(service)

    async def check():
        prepared = await server.call_tool("prepare_paper_pdf_upload", {"paper_id": "paper-1"})
        arguments = {"paper_id": "paper-1", "version_id": "version-1",
                     "confirmation_token": prepared.structuredContent["confirmation_token"]}
        result = await server.call_tool("upload_paper_pdf_to_server", arguments)
        assert result.isError is True
        assert result.structuredContent is None
        assert result.content[0].text.startswith(code + ":")
        assert "private-detail" not in result.model_dump_json()
        repeated = await server.call_tool("upload_paper_pdf_to_server", arguments)
        assert repeated.isError is True
        assert repeated.content[0].text.startswith("PDF_UPLOAD_CONFIRMATION_REQUIRED:")

    asyncio.run(check())
    service.upload.assert_called_once_with(preview())
