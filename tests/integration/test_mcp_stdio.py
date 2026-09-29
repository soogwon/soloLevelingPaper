"""실제 자식 프로세스의 stdio MCP 초기화와 도구 목록을 검증한다."""

import asyncio
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_server_lists_tools(tmp_path):
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment.update({
        'PYTHONPATH': str(root / 'src'),
        'DATA_ROOT': str(tmp_path / 'runtime'),
        'OPENAI_API_KEY': 'test-key',
        'ALLOW_EXTERNAL_API': 'false',
        'LOCAL_ONLY': 'true',
    })

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m', 'solo_leveling.interfaces.mcp'], env=environment, cwd=root)
        with (tmp_path / 'stderr.log').open('w', encoding='utf-8') as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert {tool.name for tool in tools.tools} == {
                        'add_paper', 'get_paper_status', 'start_learning',
                        'ask_paper', 'get_evidence',
                    }
                    ask = next(tool for tool in tools.tools if tool.name == 'ask_paper')
                    assert ask.outputSchema is not None
                    assert ask.annotations.openWorldHint is True
                    failure = await session.call_tool('get_evidence',
                        {'context_id': 'missing', 'evidence_ids': ['missing']})
                    assert failure.isError is True
                    message = failure.content[0].text
                    assert 'NOT_FOUND' in message
                    assert str(tmp_path) not in message

    asyncio.run(scenario())
    assert (tmp_path / 'runtime/solo_leveling.sqlite3').is_file()
