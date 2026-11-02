"""임시 SQLite와 MCP 세션으로 퀴즈 조회·제출·재전송을 검증한다."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

from solo_leveling.infrastructure.database import repository
from solo_leveling.interfaces.mcp.quiz_contracts import QuizContractAdapter, SubmitInput
from solo_leveling.interfaces.mcp.quiz_tools import StoredQuizService
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from tests.unit.test_quiz_submit_once import counts, ctx, question, setup_db  # noqa: F401


def test_submission_accepts_inclusive_limits_and_counts_utf8_bytes():
    answers = [{'question_index': i, 'text': '답'} for i in range(64)]
    answers[0]['text'] = '😀' * 5000
    args = dict(context_id='c', step='preview', quiz_id='q', request_key='k', answers=answers)
    baseline = SubmitInput.model_validate(args)
    # 한글과 이모지가 있는 제출을 정확히 64 KiB로 맞춘 뒤 한 바이트만 추가한다.
    args['context_id'] += 'a' * (65536 - len(baseline.model_dump_json().encode('utf-8')))
    assert SubmitInput.model_validate(args).answer_mapping()[0] == '😀' * 5000
    args['context_id'] += 'a'
    with pytest.raises(ValidationError):
        SubmitInput.model_validate(args)


@pytest.mark.parametrize('answers', [
    [{'question_index': i, 'selected_index': 0} for i in range(65)],
    [{'question_index': 0, 'text': '가' * 5001}],
])
def test_submission_rejects_field_limits_even_below_byte_budget(answers):
    with pytest.raises(ValidationError):
        SubmitInput(context_id='c', step='preview', quiz_id='q', request_key='k', answers=answers)


def test_quiz_protocol_with_persisted_replay(ctx):
    db, cid, qid = ctx

    def server():
        return create_server(MCPServices(Mock(), Mock(), quiz=QuizContractAdapter(StoredQuizService(db))))

    async def scenario():
        async with create_connected_server_and_client_session(server()) as client:
            quiz = await client.call_tool('get_quiz', {'context_id': cid, 'step': 'preview'})
            assert not quiz.isError and quiz.structuredContent['quiz_id'] == qid
            assert json.loads(quiz.content[0].text) == quiz.structuredContent
            assert set(quiz.structuredContent['questions'][0]) == {
                'question_index', 'question_type', 'prompt', 'choices'}
            args = dict(context_id=cid, step='preview', quiz_id=qid, request_key='k1',
                        answers=[{'question_index': 0, 'selected_index': 1}])
            invalid = await client.call_tool('submit_quiz', dict(args, answers=[
                {'question_index': False, 'text': 'PRIVATE_MARKER'}]))
            assert invalid.isError and 'INVALID_ARGUMENT' in invalid.content[0].text
            assert 'PRIVATE_MARKER' not in invalid.content[0].text
            for answers in (
                [{'question_index': 0, 'selected_index': 99}],
                [{'question_index': i, 'selected_index': 0} for i in range(65)],
                [{'question_index': 0, 'text': '가' * 5001}],
                [{'question_index': i, 'text': '가' * 5000} for i in range(5)],
            ):
                rejected = await client.call_tool('submit_quiz', dict(args, answers=answers))
                assert rejected.isError and 'INVALID_ARGUMENT' in rejected.content[0].text
                assert counts(db) == (0, 0, 0)
            assert counts(db) == (0, 0, 0)
            saved = await client.call_tool('submit_quiz', args)
            assert not saved.isError
            first = saved.structuredContent
            assert not first['reused'] and first['score'] == 1 and first['attempt_id']

        repository.save_quiz(db, cid, 'preview', [question()])
        async with create_connected_server_and_client_session(server()) as client:
            replay = await client.call_tool('submit_quiz', args)
            assert not replay.isError and replay.structuredContent == dict(first, reused=True)
            conflict = await client.call_tool('submit_quiz', dict(args, answers=[
                {'question_index': 0, 'selected_index': 0}]))
            assert conflict.isError and 'REQUEST_CONFLICT' in conflict.content[0].text
            stale = await client.call_tool('submit_quiz', dict(args, request_key='k2'))
            assert stale.isError and 'QUIZ_CHANGED' in stale.content[0].text
            assert counts(db) == (1, 1, 1)

    asyncio.run(scenario())
