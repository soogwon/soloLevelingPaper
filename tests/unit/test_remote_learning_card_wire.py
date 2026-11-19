"""실제 FastMCP의 구조화 응답 계약을 검사한다."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from solo_leveling.interfaces.mcp import remote_quiz, remote_reflection
from tests.unit.test_remote_annotation_tools import token


def test_learning_cards_emit_structured_results(monkeypatch):
    async def verify():
        server = FastMCP('합성 학습 카드')
        credential = token(['reflections:read', 'reflections:write', 'quizzes:read', 'quizzes:write'])
        for module in (remote_quiz, remote_reflection):
            monkeypatch.setattr(module, 'get_access_token', lambda: credential)
        queries = SimpleNamespace(owner=lambda: 'alice', evidence=lambda *_: {'status': 'not_found'})
        reflection, quiz = Mock(), Mock()
        reflection.questions.return_value = {'question_revision': 'r1'}
        reflection.consent.return_value = {'question_revision': 'r1'}
        quiz.consent.return_value = {'storage': 'current'}
        reflection.save.return_value = quiz.get.return_value = quiz.submit.return_value = {'synthetic': True}
        remote_reflection.register_remote_reflection(server, queries, reflection)
        remote_quiz.register_remote_quiz(server, queries, quiz)
        ctx, quiz_id, key, attempt_id = map(str, (uuid4(), uuid4(), uuid4(), uuid4()))
        quiz.attempt.return_value = {'quiz_id': quiz_id, 'attempt_id': attempt_id,
                                     'request_key': key, 'results': []}
        quiz.scores.return_value = []
        quiz.attempt_by_key.return_value = {'attempt': None}
        async def call(name, **args):
            text, structured = await server.call_tool(name, {'context_id': ctx, **args})
            assert text and structured['status'] == 'ok'
            return structured['result']
        reflection_view = await call('show_remote_reading_reflection')
        await call('save_remote_reflection_card', expected_binding=reflection_view['card_binding'],
                   request_key=key, question_revision='r1', template_id='before', body='합성',
                   status='completed', expected_revision=0, storage_consent_id='test')
        quiz_view = await call('show_remote_quiz', quiz_id=quiz_id)
        bound = dict(quiz_id=quiz_id, expected_binding=quiz_view['binding'])
        await call('submit_remote_quiz_card', **bound, request_key=key, answers={'0': 1},
                   storage_consent_id='test')
        await call('get_remote_quiz_submission', **bound, request_key=key)
        await call('show_remote_quiz_review', attempt_id=attempt_id)
        await call('get_remote_quiz_card_review', **bound, attempt_id=attempt_id)
    asyncio.run(verify())
