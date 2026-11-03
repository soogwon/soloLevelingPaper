"""#145 저장소 최적화 전후에 유지할 실제 MCP 이력 조회 계약을 검증한다."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.interfaces.mcp.quiz_contracts import QuizContractAdapter
from solo_leveling.interfaces.mcp.quiz_tools import StoredQuizService
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from tests.unit.test_quiz_submit_once import ctx, make_attempt, setup_db  # noqa: F401


SUMMARY_FIELDS = {
    'attempt_id', 'context_id', 'quiz_id', 'step', 'submitted_at',
    'correct_count', 'total_count', 'score',
}


def server(db):
    return create_server(MCPServices(Mock(), Mock(), quiz=QuizContractAdapter(StoredQuizService(db))))


def save(ctx, attempt_id, *, step='preview', timestamp=None):
    db, cid, qid = ctx
    attempt = replace(make_attempt(cid, qid, attempt_id), step=step)
    if timestamp is not None:
        attempt = replace(attempt, submitted_at=timestamp)
    return repo.save_quiz_attempt(db, attempt)


async def page(client, cid, **kwargs):
    result = await client.call_tool('list_quiz_attempts', {'context_id': cid, **kwargs})
    assert not result.isError
    data = result.structuredContent
    assert json.loads(result.content[0].text) == data
    assert set(data) == {'context_id', 'items', 'next_cursor'}
    assert data['context_id'] == cid
    for item in data['items']:
        # 답안 본문·정답·근거가 목록에 섞이지 않도록 공개 필드를 확인한다.
        assert set(item) == SUMMARY_FIELDS
        assert item['context_id'] == cid
    return data


def test_pages_keep_tie_order_when_new_attempt_arrives(ctx):
    db, cid, _ = ctx
    # ID 사전순과 저장 순서를 다르게 하여 잘못된 동률 정렬을 잡는다.
    save(ctx, 'z-old')
    save(ctx, 'a-middle')
    save(ctx, 'm-new')

    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            first = await page(client, cid, step='preview', limit=2)
            assert [r['attempt_id'] for r in first['items']] == ['m-new', 'a-middle']
            assert first['next_cursor'] == 'a-middle'
            scores = await client.call_tool('get_quiz_scores', {'context_id': cid})
            assert not scores.isError
            score = next(r for r in scores.structuredContent['scores'] if r['step'] == 'preview')
            assert score['latest_attempt_id'] == 'm-new'
            # 같은 시각의 새 제출도 이미 받은 커서 뒤에 끼어들지 않는다.
            save(ctx, 'b-arrived')
            last = await page(client, cid, step='preview', limit=2, cursor=first['next_cursor'])
            assert [r['attempt_id'] for r in last['items']] == ['z-old']
            assert last['next_cursor'] is None
            assert len({r['attempt_id'] for r in first['items'] + last['items']}) == 3
            refreshed = await page(client, cid, step='preview', limit=1)
            assert refreshed['items'][0]['attempt_id'] == 'b-arrived'
            scores = await client.call_tool('get_quiz_scores', {'context_id': cid})
            assert not scores.isError
            score = next(r for r in scores.structuredContent['scores'] if r['step'] == 'preview')
            assert score['latest_attempt_id'] == 'b-arrived'
            empty = await page(client, cid, cursor='z-old')
            assert empty['items'] == [] and empty['next_cursor'] is None

    asyncio.run(scenario())


def test_default_and_maximum_page_sizes_only_return_summaries(ctx):
    db, cid, _ = ctx
    for i in range(101):
        save(ctx, f'attempt-{i:03}')

    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            default = await page(client, cid)
            assert len(default['items']) == 20 and default['next_cursor'] == 'attempt-081'
            maximum = await page(client, cid, limit=100)
            assert len(maximum['items']) == 100 and maximum['next_cursor'] == 'attempt-001'
            assert [r['attempt_id'] for r in maximum['items']] == [f'attempt-{i:03}' for i in range(100, 0, -1)]
            tail = await page(client, cid, limit=100, cursor=maximum['next_cursor'])
            assert [r['attempt_id'] for r in tail['items']] == ['attempt-000']
            assert tail['next_cursor'] is None

    asyncio.run(scenario())


def test_step_filter_and_timestamp_order(ctx):
    db, cid, _ = ctx
    save(ctx, 'newer-time', timestamp='2026-11-02T00:00:00+00:00')
    save(ctx, 'later-insert', timestamp='2026-11-01T00:00:00+00:00')
    save(ctx, 'review-only', step='review')

    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            data = await page(client, cid, step='preview')
            assert [r['attempt_id'] for r in data['items']] == ['newer-time', 'later-insert']
            assert all(r['step'] == 'preview' for r in data['items'])
            empty = await page(client, cid, step='structure')
            assert empty == {'context_id': cid, 'items': [], 'next_cursor': None}

    asyncio.run(scenario())


@pytest.mark.parametrize('case', ['missing', 'wrong_step', 'wrong_context'])
def test_invalid_cursor_is_safe_and_does_not_modify_records(ctx, case):
    db, cid, _ = ctx
    save(ctx, 'private-cursor')
    other = replace(repo.get_learning_context(db, cid), context_id='other')
    repo.create_learning_context(db, other)
    args = {'context_id': cid, 'step': 'preview', 'cursor': 'private-cursor'}
    if case == 'missing':
        args['cursor'] = 'missing-private-cursor'
    elif case == 'wrong_step':
        args['step'] = 'review'
    else:
        args['context_id'] = 'other'
    before = Path(db).read_bytes()

    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            result = await client.call_tool('list_quiz_attempts', args)
            assert result.isError
            text = '\n'.join(c.text for c in result.content if c.type == 'text')
            assert 'INVALID_ARGUMENT' in text
            assert args['cursor'] not in text and 'First.' not in text
            assert (await page(client, 'other'))['items'] == []
            assert [r['attempt_id'] for r in (await page(client, cid))['items']] == ['private-cursor']

    asyncio.run(scenario())
    assert Path(db).read_bytes() == before


@pytest.mark.parametrize('limit', [0, -1, 101])
def test_invalid_page_size_is_rejected(ctx, limit):
    db, cid, _ = ctx

    async def scenario():
        async with create_connected_server_and_client_session(server(db)) as client:
            result = await client.call_tool('list_quiz_attempts', {'context_id': cid, 'limit': limit})
            assert result.isError and 'INVALID_ARGUMENT' in result.content[0].text

    asyncio.run(scenario())
