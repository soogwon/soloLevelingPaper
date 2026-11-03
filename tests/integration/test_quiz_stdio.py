"""실제 MCP 프로세스를 재시작해 퀴즈 제출 결과의 영속성과 응답 경계를 검증한다."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from solo_leveling.infrastructure.database import repository
from tests.unit.test_quiz_submit_once import counts, ctx, question, setup_db  # noqa: F401


def test_quiz_replay_after_real_stdio_server_restart(ctx, tmp_path):
    db, cid, qid = ctx
    root = Path(__file__).resolve().parents[2]
    # 사용자 설정과 키를 상속하지 않고 합성 DB만 사용한다.
    environment = {key: os.environ[key] for key in (
        'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR',
    ) if key in os.environ}
    environment.update(
        PYTHONPATH=str(root / 'src'), PYTHONUTF8='1',
        DATA_ROOT=str(tmp_path), SQLITE_FILENAME=Path(db).name,
        SOLO_LEVELING_ENV_FILE=str(tmp_path / 'empty.env'),
        LOCAL_ONLY='true', ALLOW_EXTERNAL_API='false', OPENAI_API_KEY='test-only',
    )
    (tmp_path / 'empty.env').write_text('', encoding='utf-8')
    arguments = dict(context_id=cid, step='preview', quiz_id=qid, request_key='request-1',
                     answers=[{'question_index': 0, 'selected_index': 0}])

    @asynccontextmanager
    async def connect(label):
        parameters = StdioServerParameters(
            command=sys.executable, args=['-B', '-m', 'solo_leveling.interfaces.mcp'],
            env=environment, cwd=tmp_path,
        )
        with (tmp_path / f'{label}.stderr').open('w', encoding='utf-8') as errors:
            async with stdio_client(parameters, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    async def success(session, name, args):
        result = await session.call_tool(name, args)
        assert not result.isError, result.content
        assert json.loads(result.content[0].text) == result.structuredContent
        return result.structuredContent

    async def rejected(session, name, args, code):
        result = await session.call_tool(name, args)
        assert result.isError
        text = '\n'.join(item.text for item in result.content if item.type == 'text')
        assert code in text
        for private in ('PRIVATE_MARKER', str(tmp_path), 'First.'):
            assert private not in text

    async def scenario():
        async with connect('first') as session:
            quiz = await success(session, 'get_quiz', dict(context_id=cid, step='preview'))
            assert quiz['quiz_id'] == qid
            assert set(quiz['questions'][0]) == {
                'question_index', 'question_type', 'prompt', 'choices',
            }
            first = await success(session, 'submit_quiz', arguments)
            assert first['reused'] is False and first['score'] == 0
            assert counts(db) == (1, 1, 1)

        # 서버 종료 후 퀴즈를 교체해도 완료된 동일 요청은 원래 결과를 돌려줘야 한다.
        repository.save_quiz(db, cid, 'preview', [question()])
        async with connect('restarted') as session:
            replay = await success(session, 'submit_quiz', arguments)
            assert replay == dict(first, reused=True)
            await rejected(session, 'submit_quiz', dict(arguments, answers=[
                {'question_index': 0, 'selected_index': 1},
            ]), 'REQUEST_CONFLICT')
            await rejected(session, 'submit_quiz', dict(arguments, request_key='new-request'),
                           'QUIZ_CHANGED')
            await rejected(session, 'submit_quiz', dict(arguments, answers=[
                {'question_index': False, 'text': 'PRIVATE_MARKER'},
            ]), 'INVALID_ARGUMENT')
            assert counts(db) == (1, 1, 1)

            detail_args = dict(context_id=cid, attempt_id=first['attempt_id'])
            detail = await success(session, 'get_quiz_attempt', detail_args)
            assert detail == {key: value for key, value in first.items() if key != 'reused'}
            history = await success(session, 'list_quiz_attempts', dict(context_id=cid, limit=1))
            assert [item['attempt_id'] for item in history['items']] == [first['attempt_id']]
            assert history['next_cursor'] is None
            scores = await success(session, 'get_quiz_scores', dict(context_id=cid))
            preview = next(item for item in scores['scores'] if item['step'] == 'preview')
            assert preview['attempt_count'] == 1 and preview['latest_score'] == 0
            review = await success(session, 'review_quiz_attempt', detail_args)
            assert review['items'][0]['evidence'][0]['quote_original'] == 'First.'
            for tool in ('get_quiz_attempt', 'review_quiz_attempt'):
                await rejected(session, tool, dict(detail_args, context_id='other'), 'NOT_FOUND')
        assert counts(db) == (1, 1, 1)

    asyncio.run(asyncio.wait_for(scenario(), timeout=60))
