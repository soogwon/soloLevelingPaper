import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from mcp.server.fastmcp.exceptions import ToolError

from solo_leveling.interfaces.mcp.question_card import QuestionKeys
from solo_leveling.interfaces.mcp.server import MCPServices, create_server


def test_saved_keys_survive_reopening_and_are_scoped_to_context(tmp_path):
    path = tmp_path / 'requests.sqlite3'
    keys = QuestionKeys(path)
    assert keys.recent('ctx') == []
    assert not path.exists()
    first = keys.reserve('ctx')['request_key']
    second = keys.reserve('ctx')['request_key']
    keys.reserve('other')
    reopened = QuestionKeys(path)
    assert [row['request_key'] for row in reopened.recent('ctx')] == [second, first]
    assert reopened.recent('unknown') == []


def test_open_card_requires_existing_context(tmp_path):
    contexts = SimpleNamespace(get_context=lambda value: object() if value == 'ctx' else None)
    server = create_server(MCPServices(
        Mock(), Mock(), contexts=contexts, executions=Mock(),
        question_keys=QuestionKeys(tmp_path / 'requests.sqlite3')))

    async def scenario():
        result = await server.call_tool('open_paper_question_card', {'context_id': 'ctx'})
        assert result[1] == {'context_id': 'ctx'}
        try:
            await server.call_tool('open_paper_question_card', {'context_id': 'missing'})
        except ToolError as error:
            assert 'NOT_FOUND' in str(error)
        else:
            raise AssertionError('존재하지 않는 context_id가 허용되었습니다.')

    asyncio.run(scenario())
