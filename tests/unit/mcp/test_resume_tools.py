"""이어하기 코드는 읽기 전용 MCP 도구로만 해석된다."""

from unittest.mock import Mock

from solo_leveling.interfaces.mcp.server import MCPServices, create_server


def make_server(resolver):
    return create_server(MCPServices(Mock(), Mock(), resume=resolver))


def test_resume_tool_registers_with_read_only_structured_contract():
    resolver = Mock()
    resolver.resolve.return_value = {
        "resume_request_id": "request-1", "paper_id": "paper-1",
        "version_id": "version-1", "learning_record_id": "record-1",
        "file_access_status": "local_check_required", "status": "prepared",
    }
    server = make_server(resolver)

    tool = server._tool_manager._tools["resolve_resume_request"]
    assert tool.annotations.readOnlyHint and tool.annotations.idempotentHint
    assert not tool.annotations.destructiveHint
    assert tool.parameters["required"] == ["code"]
    assert set(tool.output_schema["required"]) >= {
        "resume_request_id", "paper_id", "version_id", "learning_record_id",
        "file_access_status", "status",
    }
    resolver.resolve.assert_not_called()
