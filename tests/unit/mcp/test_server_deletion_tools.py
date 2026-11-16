"""삭제 카드 확인·대상 고정·조회와 접수 분리를 검증한다."""

import asyncio
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.interfaces.mcp.server import MCPServices, create_server


def test_confirmation_resource_and_read_only_status():
    service = Mock()
    service.preview.return_value = {"paper_id": "p1", "title": "합성 논문", "connection_id": "c1"}
    service.confirm.return_value = {
        "paper_id": "p1",
        "status": "blocking",
        "backup_erasure_confirmed": False,
        "local_data_preserved": True,
    }
    service.status.return_value = {
        "paper_id": "p1",
        "status": "cleaning",
        "backup_erasure_confirmed": False,
        "local_data_preserved": True,
    }
    server = create_server(MCPServices(Mock(), Mock(), server_deletion=service))

    async def check():
        tools = {tool.name: tool for tool in await server.list_tools()}
        for name in ("confirm_server_paper_deletion", "retry_server_paper_deletion"):
            assert tools[name].meta["ui"]["visibility"] == ["app"]
            assert tools[name].annotations.destructiveHint
        assert tools["get_server_paper_deletion"].annotations.readOnlyHint
        result = await server.call_tool("prepare_server_paper_deletion", {"paper_id": "p1"})
        token = result.structuredContent["confirmation_token"]
        service.confirm.assert_not_called()
        service.retry.assert_not_called()
        with pytest.raises(ToolError):
            await server.call_tool("confirm_server_paper_deletion", {"paper_id": "p2", "confirmation_token": token})
        service.confirm.assert_not_called()
        # 잘못된 대상에 사용된 토큰은 재사용하지 않고 새 안내를 받는다.
        result = await server.call_tool("prepare_server_paper_deletion", {"paper_id": "p1"})
        args = {"paper_id": "p1", "confirmation_token": result.structuredContent["confirmation_token"]}
        await server.call_tool("confirm_server_paper_deletion", args)
        with pytest.raises(ToolError):
            await server.call_tool("confirm_server_paper_deletion", args)
        await server.call_tool("get_server_paper_deletion", {"paper_id": "p1"})
        service.confirm.assert_called_once_with(service.preview.return_value)
        service.retry.assert_not_called()
        resource = await server.read_resource("ui://solo-leveling/server-deletion.html")
        assert "서버 논문 삭제" in resource[0].content
        assert "<script src=" not in resource[0].content

    asyncio.run(check())
