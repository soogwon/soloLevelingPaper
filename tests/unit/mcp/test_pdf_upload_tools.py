"""PDF 원문은 전용 카드의 명시적 승인 뒤에만 서버로 전송된다."""

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.application.server_sync.pdf_upload import PdfUploadPreview, PdfUploadResult
from solo_leveling.interfaces.mcp.pdf_upload_tools import PDF_UPLOAD_APP_URI
from solo_leveling.interfaces.mcp.server import MCPServices, create_server


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
        assert result[1]["status"] == "ready"
        with pytest.raises(ToolError):
            await server.call_tool("upload_paper_pdf_to_server", {
                "paper_id": "paper-1", "version_id": "version-1",
                "confirmation_token": prepared.meta["confirmation_token"],
            })

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
