"""실제 MCP stdio에서 저장된 학습 맥락과 텍스트 폴백을 함께 검증한다."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from solo_leveling.infrastructure.database import repository as repo
from tests.unit.test_learning_persistence import setup_db, publish


def test_dashboard_reads_saved_context_over_stdio(setup_db, tmp_path):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.complete_step(db, context.context_id, 'preview')
    repo.create_learning_context(db, replace(context, context_id='other', goal='skim'))
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONPATH=str(root / 'src'), DATA_ROOT=str(tmp_path),
        SQLITE_FILENAME=Path(db).name, LOCAL_ONLY='true', ALLOW_EXTERNAL_API='false',
        OPENAI_API_KEY='test-key', HF_HUB_OFFLINE='1', ANONYMIZED_TELEMETRY='false')

    async def run():
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'solo_leveling.interfaces.mcp'], env=environment, cwd=root)
        with (tmp_path / 'stderr.log').open('w') as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    before = Path(db).read_bytes()
                    data = await session.call_tool('get_learning_dashboard', {'context_id': 'ctx'})
                    assert not data.isError
                    assert data.structuredContent['progress']['completion_ratio'] == .25
                    assert data.structuredContent['processing']['job_id'] == 'job'
                    assert json.loads(data.content[0].text) == data.structuredContent
                    other = await session.call_tool('get_learning_dashboard', {'context_id': 'other'})
                    assert not other.isError
                    assert other.structuredContent['progress']['recorded'] is False
                    assert other.structuredContent['summary'] is None
                    bad = await session.call_tool('get_learning_dashboard', {'context_id': 'missing'})
                    assert bad.isError and 'NOT_FOUND' in bad.content[0].text
                    assert Path(db).read_bytes() == before

    asyncio.run(asyncio.wait_for(run(), timeout=60))
