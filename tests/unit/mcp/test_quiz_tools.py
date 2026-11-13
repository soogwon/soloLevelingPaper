"""임시 SQLite와 MCP 세션으로 퀴즈 조회·제출·재전송을 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
import asyncio
import json
from unittest.mock import Mock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

from solo_leveling.infrastructure.database import repository
from solo_leveling.application.evidence_qa.ports import GenerationUnavailable
from solo_leveling.application.quiz.service import DraftQuestion, QuizService
from solo_leveling.interfaces.mcp.quiz_contracts import QuizContractAdapter, SubmitInput
from solo_leveling.interfaces.mcp.quiz_tools import StoredQuizService
from solo_leveling.interfaces.mcp.server import MCPServices, create_server
from tests.unit.test_quiz_submit_once import counts, ctx, question, setup_db, submit  # noqa: F401
from tests.unit.test_learning_persistence import publish


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


def test_history_cursor_and_latest_score_agree_for_equal_timestamps(ctx):
    db, cid, qid = ctx
    first = submit(db, 'key1', cid, qid, attempt_id='z-first')
    second = submit(db, 'key2', cid, qid, attempt_id='a-second', selected=0)
    assert first.attempt.submitted_at == second.attempt.submitted_at
    adapter = QuizContractAdapter(StoredQuizService(db))
    args = dict(context_id=cid, step='preview', limit=1)
    score = next(s for s in adapter.scores({'context_id': cid}).scores if s.step == 'preview')
    page = adapter.list_attempts(args)
    assert page.items[0].attempt_id == score.latest_attempt_id == 'a-second'
    assert page.next_cursor == 'a-second'
    last = adapter.list_attempts(dict(args, cursor=page.next_cursor))
    assert [a.attempt_id for a in last.items] == ['z-first']
    assert last.next_cursor is None


def test_quiz_protocol_with_persisted_replay(ctx):
    db, cid, qid = ctx

    def server():
        return create_server(MCPServices(Mock(), Mock(), quiz=QuizContractAdapter(StoredQuizService(db))))

    async def scenario():
        async with create_connected_server_and_client_session(server()) as client:
            tools = (await client.list_tools()).tools
            for name in ('get_quiz', 'generate_quiz', 'submit_quiz', 'get_quiz_attempt'):
                tool = next(tool for tool in tools if tool.name == name)
                assert tool.meta['ui']['resourceUri'] == 'ui://solo-leveling/quiz.html'
                assert '$ref' not in json.dumps(tool.inputSchema)
                assert '$defs' not in tool.inputSchema
            resource = await client.read_resource('ui://solo-leveling/quiz.html')
            assert resource.contents[0].mimeType == 'text/html;profile=mcp-app'
            assert '논문 학습 퀴즈' in resource.contents[0].text
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
            assert 'INVALID_INPUT' in invalid.content[0].text
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
            detail_args = dict(context_id=cid, attempt_id=first['attempt_id'])
            detail = await client.call_tool('get_quiz_attempt', detail_args)
            assert detail.structuredContent == {k: v for k, v in first.items() if k != 'reused'}
            denied = await client.call_tool('get_quiz_attempt', dict(detail_args, context_id='other'))
            assert denied.isError and 'NOT_FOUND' in denied.content[0].text
            current = repository.get_quiz(db, cid, 'preview')
            second = await client.call_tool('submit_quiz', dict(args, quiz_id=current.quiz_id, request_key='k2',
                answers=[{'question_index': 0, 'selected_index': 0}]))
            score_response = await client.call_tool('get_quiz_scores', {'context_id': cid})
            scores = {row['step']: row for row in score_response.structuredContent['scores']}
            assert set(scores) == {'preview', 'structure', 'deep_read', 'review'}
            assert scores['preview']['attempt_count'] == 2
            assert scores['preview']['latest_score'] == 0 and scores['preview']['best_score'] == 1
            assert scores['review']['attempt_count'] == 0 and scores['review']['latest_score'] is None
            review_args = dict(context_id=cid, attempt_id=second.structuredContent['attempt_id'])
            review = await client.call_tool('review_quiz_attempt', review_args)
            item, = review.structuredContent['items']
            assert item['result']['correct_answer'] == '나' and item['result']['selected_index'] == 0
            assert item['evidence'][0]['quote_original'] == 'First.'
            assert item['evidence'][0]['evidence_id'] == 'e1'
            denied = await client.call_tool('review_quiz_attempt', dict(review_args, context_id='other'))
            assert denied.isError and 'NOT_FOUND' in denied.content[0].text
            assert 'First.' not in denied.content[0].text
            correct = await client.call_tool('review_quiz_attempt', detail_args)
            assert correct.structuredContent['items'] == []
            page_args = dict(context_id=cid, step='preview', limit=1)
            page = (await client.call_tool('list_quiz_attempts', page_args)).structuredContent
            newest = second.structuredContent['attempt_id']
            assert page['next_cursor'] == newest
            assert [item['attempt_id'] for item in page['items']] == [newest]
            assert 'results' not in page['items'][0]
            last = (await client.call_tool('list_quiz_attempts', dict(page_args, cursor=newest))).structuredContent
            assert last['next_cursor'] is None
            assert [item['attempt_id'] for item in last['items']] == [first['attempt_id']]
            empty = await client.call_tool('list_quiz_attempts', dict(page_args, step='review'))
            assert empty.structuredContent == dict(context_id=cid, items=[], next_cursor=None)
            invalid = await client.call_tool('list_quiz_attempts', dict(page_args, step='review', cursor=newest))
            assert invalid.isError and 'INVALID_ARGUMENT' in invalid.content[0].text

    asyncio.run(scenario())


def test_quiz_accepts_json_string_answers(ctx):
    db, cid, qid = ctx
    quiz_server = create_server(MCPServices(
        Mock(), Mock(), quiz=QuizContractAdapter(StoredQuizService(db))))

    async def scenario():
        async with create_connected_server_and_client_session(quiz_server) as client:
            result = await client.call_tool('submit_quiz', {
                'context_id': cid, 'step': 'preview', 'quiz_id': qid,
                'request_key': 'json-string',
                'answers': json.dumps([{'question_index': 0, 'selected_index': 1}]),
            })
            assert not result.isError
            assert result.structuredContent['score'] == 1

    asyncio.run(scenario())


def test_generate_quiz_creates_once_and_get_reuses_it(setup_db):
    db, chunks = setup_db
    cid = publish(db, chunks).context_id
    generator = Mock()

    def generate(step, evidence):
        return [DraftQuestion(
            'multiple_choice', '첫 문장은?', (evidence[0].evidence_id,),
            ('첫째', '둘째'), 0,
        )]

    generator.generate_quiz.side_effect = generate
    adapter = QuizContractAdapter(StoredQuizService(
        db, QuizService(db, generator, id_factory=lambda: 'evidence')))
    quiz_server = create_server(MCPServices(Mock(), Mock(), quiz=adapter))

    async def scenario():
        async with create_connected_server_and_client_session(quiz_server) as client:
            missing = await client.call_tool('get_quiz', {'context_id': cid, 'step': 'preview'})
            assert missing.isError and 'generate_quiz' in missing.content[0].text
            first = await client.call_tool(
                'generate_quiz', {'context_id': cid, 'step': 'preview'})
            replay = await client.call_tool(
                'generate_quiz', {'context_id': cid, 'step': 'preview'})
            stored = await client.call_tool(
                'get_quiz', {'context_id': cid, 'step': 'preview'})
            assert not first.isError
            assert first.structuredContent == replay.structuredContent == stored.structuredContent
            assert first.structuredContent['questions'][0]['prompt'] == '첫 문장은?'

    asyncio.run(scenario())
    generator.generate_quiz.assert_called_once()


def test_generate_quiz_reports_unavailable_provider(setup_db):
    db, chunks = setup_db
    cid = publish(db, chunks).context_id
    generator = Mock()
    generator.generate_quiz.side_effect = GenerationUnavailable('private provider detail')
    adapter = QuizContractAdapter(StoredQuizService(db, QuizService(db, generator)))
    quiz_server = create_server(MCPServices(Mock(), Mock(), quiz=adapter))

    async def scenario():
        async with create_connected_server_and_client_session(quiz_server) as client:
            result = await client.call_tool(
                'generate_quiz', {'context_id': cid, 'step': 'preview'})
            assert result.isError
            assert result.content[0].text.split(': ')[-1] == 'UPSTREAM_UNAVAILABLE'
            assert 'private' not in str(result)

    asyncio.run(scenario())


def test_concurrent_generation_reuses_quiz_across_servers(setup_db, monkeypatch):
    db, chunks = setup_db
    cid = publish(db, chunks).context_id
    generator = Mock()
    generator.generate_quiz.side_effect = lambda step, evidence: [DraftQuestion(
        'multiple_choice', '첫 문장은?', (evidence[0].evidence_id,), ('첫째', '둘째'), 0)]
    services = [StoredQuizService(db, QuizService(db, generator)) for _ in range(2)]
    get_quiz = repository.get_quiz
    readers = Barrier(2)
    state = local()

    def simultaneous_lookup(*args):
        quiz = get_quiz(*args)
        if not getattr(state, 'started', False):
            state.started = True
            readers.wait(timeout=5)
        return quiz

    monkeypatch.setattr(repository, 'get_quiz', simultaneous_lookup)
    with ThreadPoolExecutor(2) as pool:
        quizzes = list(pool.map(lambda service: service.generate_quiz(cid, 'preview'), services))
    generator.generate_quiz.assert_called_once()
    assert quizzes[0].quiz_id == quizzes[1].quiz_id == get_quiz(db, cid, 'preview').quiz_id
