"""실제 stdio 서버 재시작과 응답/조회 장애 후 제품 공개 답변 복원. API 사용 없음."""

import asyncio
from contextlib import asynccontextmanager, closing
import json
import os
from pathlib import Path
import sqlite3
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.unit.test_learning_persistence import setup_db, publish  # noqa: F401


def test_committed_response_loss_and_lookup_error_never_reexecute(setup_db, tmp_path):
    db, chunks = setup_db
    ctx = publish(db, chunks).context_id
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('CREATE TABLE probe_calls(kind TEXT NOT NULL)')
    root = Path(__file__).resolve().parents[2]
    environment = {key: os.environ[key] for key in
        ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR') if key in os.environ}
    environment.update(PYTHONPATH=os.pathsep.join((str(root/'src'), str(root))), PYTHONUTF8='1',
        PROBE_DB=str(db), LOCAL_ONLY='true', ALLOW_EXTERNAL_API='false')
    args = dict(context_id=ctx, request_key='durable-key', question='합성 질문',
                focus={'pdf_pages': [1, 2], 'strict_pages': True})

    def snapshot():
        with closing(sqlite3.connect(db)) as conn:
            return (conn.execute('SELECT status,result_json FROM ask_executions').fetchall(),
                    conn.execute('SELECT evidence_id FROM evidences ORDER BY evidence_id').fetchall(),
                    conn.execute('SELECT kind,COUNT(*) FROM probe_calls GROUP BY kind ORDER BY kind').fetchall())

    @asynccontextmanager
    async def connect(label, **flags):
        parameters = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'tests.fixtures.keyed_question_server'],
            env={**environment, **flags}, cwd=tmp_path)
        with (tmp_path/f'{label}.stderr').open('w', encoding='utf-8') as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    yield client

    async def success(client, tool, inputs):
        result = await client.call_tool(tool, inputs)
        assert not result.isError, result.content
        assert json.loads(result.content[0].text) == result.structuredContent
        return result.structuredContent

    async def failure(client, tool, inputs, code):
        result = await client.call_tool(tool, inputs)
        assert result.isError
        message = ''.join(c.text for c in result.content if c.type == 'text')
        assert code in message and 'PRIVATE' not in message and str(tmp_path) not in message

    scope = {'context_id': ctx, 'request_key': args['request_key']}

    async def scenario():
        async with connect('lost', PROBE_DROP_RESPONSE='1') as client:
            await failure(client, 'submit_paper_question', args, 'INTERNAL_ERROR')
        saved = snapshot()
        assert len(saved[0]) == 1 and saved[0][0][0] == 'completed'
        assert len(saved[1]) == 2
        assert saved[2] == [('generation', 1), ('search', 1)]
        original_answer = json.loads(saved[0][0][1])
        async with connect('restart') as client:
            restored = await success(client, 'get_paper_question_status', scope)
            assert restored['state'] == 'completed' and restored['answer'] == original_answer
            assert await success(client, 'submit_paper_question', args) == restored
            changed = {**args, 'focus': {'pdf_pages': [1, 2], 'strict_pages': False}}
            await failure(client, 'submit_paper_question', changed, 'CONFLICT')
            other = await success(client, 'get_paper_question_status', {**scope, 'context_id': 'other'})
            missing = await success(client, 'get_paper_question_status', {**scope, 'request_key': 'missing'})
            assert other == missing == {'state': 'not_found', 'failure_code': None, 'answer': None}
        assert snapshot() == saved
        async with connect('lookup-error', PROBE_LOOKUP_FAILURE='1') as client:
            await failure(client, 'get_paper_question_status', scope, 'INTERNAL_ERROR')
            assert await success(client, 'get_paper_question_status', scope) == restored
        assert snapshot() == saved

    asyncio.run(scenario())
