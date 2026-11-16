"""임시 DB와 실제 stdio 자식 프로세스로 메모 재시작 계약을 검증한다."""

import asyncio
from contextlib import asynccontextmanager, closing
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from solo_leveling.infrastructure.database.schema import get_connection
from tests.unit.test_learning_persistence import setup_db, publish  # noqa: F401


@asynccontextmanager
async def connect(db, tmp_path, label):
    root = Path(__file__).resolve().parents[2]
    environment = {key: os.environ[key] for key in (
        'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR',
    ) if key in os.environ}
    empty_env = tmp_path / 'empty.env'
    empty_env.write_text('', encoding='utf-8')
    environment.update(
        PYTHONPATH=str(root / 'src'), PYTHONUTF8='1',
        DATA_ROOT=str(Path(db).parent), SQLITE_FILENAME=Path(db).name,
        SOLO_LEVELING_ENV_FILE=str(empty_env), LOCAL_ONLY='true',
        ALLOW_EXTERNAL_API='false', OPENAI_API_KEY='synthetic-only',
        SOLO_LEVELING_EMBEDDING_WARMUP='0', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
    )
    parameters = StdioServerParameters(command=sys.executable,
        args=['-B', '-m', 'solo_leveling.interfaces.mcp'], env=environment, cwd=tmp_path)
    with (tmp_path / f'{label}.stderr').open('w', encoding='utf-8') as errors:
        async with stdio_client(parameters, errlog=errors) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def success(session, name, arguments):
    result = await session.call_tool(name, arguments)
    assert not result.isError, result.content
    assert json.loads(result.content[0].text) == result.structuredContent
    return result.structuredContent


async def rejected(session, name, arguments, code):
    result = await session.call_tool(name, arguments)
    assert result.isError
    text = '\n'.join(item.text for item in result.content if item.type == 'text')
    assert code in text
    assert 'PRIVATE_BODY' not in text


def counts(db):
    with closing(get_connection(db)) as conn:
        return tuple(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                     for table in ('annotations', 'annotation_create_requests'))


def test_annotation_retry_after_process_restart_preserves_edit_and_deletion(setup_db, tmp_path):
    db, chunks = setup_db
    ctx = publish(db, chunks)
    request = dict(context_id=ctx.context_id, kind='note', body='합성 메모 😀', request_key='stable-request')

    async def scenario():
        async with connect(db, tmp_path, 'first') as session:
            first = await success(session, 'create_annotation', {'request':request})
            assert not first['reused'] and counts(db) == (1, 1)
        # 응답을 받지 못한 호출자는 ID 대신 같은 입력·키만 가지고 재접속한다.
        async with connect(db, tmp_path, 'restart') as session:
            replay = await success(session, 'create_annotation', {'request':request})
            assert replay == dict(first, reused=True)
            annotation = replay['annotation']
            key = dict(context_id=ctx.context_id, annotation_id=annotation['annotation_id'])
            updated = await success(session, 'update_annotation', {'request':dict(
                key, expected_revision=annotation['revision'], body='수정한 합성 메모')})
        async with connect(db, tmp_path, 'after-edit') as session:
            replay = await success(session, 'create_annotation', {'request':request})
            assert replay['reused'] and replay['annotation'] == updated['annotation']
            await rejected(session, 'create_annotation', {'request':dict(request, body='PRIVATE_BODY')},
                           'REQUEST_KEY_CONFLICT')
            assert counts(db) == (1, 1)
            await success(session, 'delete_annotation', {'request':dict(
                key, expected_revision=updated['annotation']['revision'])})
        async with connect(db, tmp_path, 'after-delete') as session:
            await rejected(session, 'create_annotation', {'request':request}, 'ANNOTATION_DELETED')
            assert counts(db) == (0, 1)

    asyncio.run(scenario())
