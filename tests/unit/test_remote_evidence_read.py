"""원격 근거 도구의 인증 이전 입력 검사와 오류 은닉."""
from unittest.mock import Mock
from uuid import uuid4
from solo_leveling.interfaces.mcp.remote_product import RemoteProductQueries, register_product_queries


def test_missing_owner_does_not_read_evidence():
    queries = RemoteProductQueries(Mock())
    queries.owner = Mock(return_value=None)
    queries.evidences = Mock()
    assert queries.evidence(str(uuid4()), str(uuid4())) == {'status': 'not_found'}
    queries.evidences.get.assert_not_called()
    assert queries.evidence('invalid', str(uuid4())) == {'status': 'invalid_argument'}


def test_read_failure_never_exposes_database_or_text():
    queries = RemoteProductQueries(Mock())
    queries.owner = Mock(return_value='alice')
    queries.evidences.get = Mock(side_effect=ValueError('비밀 원문·접속 정보'))
    assert queries.evidence(str(uuid4()), str(uuid4())) == {'status': 'unavailable'}


def test_actual_mcp_tool_is_readonly_structured():
    import asyncio
    from mcp.server.fastmcp import FastMCP
    queries = Mock()
    queries.evidence.return_value = {'status': 'not_found'}
    server = FastMCP('합성 근거 조회')
    register_product_queries(server, queries)

    async def check():
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools['get_remote_paper_detail'].annotations.readOnlyHint
        assert tools['get_remote_paper_detail'].outputSchema is not None
        assert tools['get_remote_evidence'].annotations.readOnlyHint
        assert tools['get_remote_evidence'].outputSchema is not None
        result = await server.call_tool('get_remote_evidence', {'context_id': str(uuid4()), 'evidence_id': str(uuid4())})
        assert result[1] == {'status': 'not_found'}
    asyncio.run(check())
