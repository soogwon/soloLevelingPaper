"""실제 격리 DB와 대역 전송으로 생성·사용량·원자 저장·재시작을 검증한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
from dataclasses import replace
import time
import json
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_quiz_intake_postgres import setup as intake_setup
from solo_leveling.application.quiz.remote_contract import RemoteQuizGenerationInput
from solo_leveling.interfaces.http.remote_quiz_intake import PostgresQuizIntake
from solo_leveling.interfaces.http.remote_quiz import PostgresRemoteQuiz
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
from solo_leveling.workers.remote_quiz import PersistedQuizExecutor, RemoteQuizRunner

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def setup(pool, db, stores, *, expires=False):
    request, storage, ai, policy = intake_setup(db, stores, pool)
    if expires:
        policy = replace(policy, timeout_seconds=1, stage_timeout_seconds=1)
    raw = json.loads(request.encoded)
    raw.update(provider='openai', prompt_version='remote-quiz-generation-v1')
    request = RemoteQuizGenerationInput(json.dumps(raw))
    db('UPDATE server_remote_consents SET input_hash=%s WHERE consent_id=ANY(%s)', (request.input_hash, [storage, ai]))
    config = {k: raw[k] for k in ('provider','model','prompt_version','language','budget_policy_version')}
    key = str(uuid4())
    saved = PostgresQuizIntake(pool, settings=config).submit('alice', key, request, storage, ai, policy=policy, window_key='test')
    item = dict(question_type='multiple_choice', prompt='합성 질문?', choices=['첫째','둘째'], correct_index=0,
                answer='첫째', grading_criteria='', evidence_ids=[request.unpack()[1][0].evidence_id])
    envelope = dict(status='completed', output=[dict(type='message', role='assistant', status='completed',
        content=[dict(type='output_text', text=json.dumps(dict(items=[item])))])], usage=dict(input_tokens=20, output_tokens=10))
    post = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: envelope, close=Mock()))
    semantic = Mock(return_value=True)
    settings = OpenAIGenerationSettings('synthetic', model='fixed', max_output_tokens=100, allow_external_api=True)
    execute = PersistedQuizExecutor(pool, settings=settings, token_upper_bound=lambda *_:100,
                                   window_key='test', validate_semantics=semantic, post=post)
    return saved, request, ai, post, semantic, execute


def test_concurrent_generation_stores_one_quiz_without_exposing_answers(pool, db, stores):
    saved, request, _, post, semantic, execute = setup(pool, db, stores)
    runner = RemoteQuizRunner(pool, execute)
    with ThreadPoolExecutor(max_workers=2) as workers:
        status = list(workers.map(lambda _:runner.run('alice', saved.execution_id), range(2)))
    assert sorted(status) == ['finished','not_claimed']
    assert post.call_count == semantic.call_count == 1
    row = db('SELECT payload_json FROM server_remote_executions WHERE execution_id=%s', (saved.execution_id,))[0]
    payload = json.loads(row['payload_json'])
    assert 'answer' not in json.dumps(payload)
    assert len(db('SELECT quiz_id FROM server_remote_quizzes WHERE quiz_id=%s', (payload['quiz_id'],))) == 1
    assert RemoteQuizRunner(pool, execute).run('alice', saved.execution_id) == 'not_claimed'
    assert post.call_count == 1
    service = PostgresRemoteQuiz(pool)
    ctx = request.unpack()[0].context_id
    restored_generation = service.generation_result('alice', ctx, saved.lookup.request_key)
    assert restored_generation['quiz'] == payload
    assert service.generation_result('bob', ctx, saved.lookup.request_key) is None
    consent = service.consent('alice', ctx, payload['quiz_id'])
    consent = service.grant_storage('alice', ctx, payload['quiz_id'], consent['notice_version'], consent['quiz_revision'])
    key = str(uuid4())
    attempt = service.submit('alice', ctx, payload['quiz_id'], key, {0:1}, consent['consent_id'])
    restored = service.attempt_by_key('alice', ctx, payload['quiz_id'], key)['attempt']
    assert restored == attempt and restored['correct_count'] == 0
    assert service.attempt_by_key('bob', ctx, payload['quiz_id'], key) is None
    assert post.call_count == 1


@pytest.mark.parametrize('failure', ['revoked','cancelled','expired','semantic','unknown','late_cancel','late_lease','late_revoke','foreign'])
def test_failure_never_publishes_or_resends(pool, db, stores, failure):
    saved, request, ai, post, semantic, execute = setup(pool, db, stores, expires=failure=='expired')
    if failure == 'revoked':
        db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s', (ai,))
    if failure == 'cancelled':
        PostgresRemoteExecutionStore(pool).request_cancel('alice', saved.execution_id)
    if failure == 'expired':
        time.sleep(1.05)
    if failure == 'semantic':
        semantic.return_value = False
    if failure == 'unknown':
        post.side_effect = TimeoutError('합성 전송 오류')
    if failure == 'foreign':
        post.return_value.json().get('output')[0]['content'][0]['text'] = json.dumps(dict(items=[]))
    if failure.startswith('late_'):
        response = post.return_value
        def late(*args, **kwargs):
            if failure == 'late_cancel':
                PostgresRemoteExecutionStore(pool).request_cancel('alice', saved.execution_id)
            elif failure == 'late_revoke':
                db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s', (ai,))
            else:
                db('UPDATE server_remote_executions SET lease_token=%s WHERE execution_id=%s', (str(uuid4()), saved.execution_id))
            return response
        post.side_effect = late
    runner = RemoteQuizRunner(pool, execute)
    assert runner.run('bob', saved.execution_id) == 'not_found'
    assert runner.run('alice', saved.execution_id) != 'finished'
    assert not db('SELECT quiz_id FROM server_remote_quizzes WHERE context_id=%s', (request.unpack()[0].context_id,))
    runner.run('alice', saved.execution_id)
    assert post.call_count <= 1
    if failure in ('revoked','cancelled','expired'):
        assert post.call_count == 0
        rows = db('SELECT remaining_input_tokens FROM server_question_budget_reservations WHERE execution_id=%s', (saved.execution_id,))
        assert rows[0]['remaining_input_tokens'] == 0


def test_quiz_insert_rolls_back_when_execution_completion_fails(pool, db, stores, monkeypatch):
    saved, request, _, post, _, execute = setup(pool, db, stores)
    monkeypatch.setattr(PostgresRemoteExecutionStore, 'complete', Mock(side_effect=ValueError('합성 저장 실패')))
    assert RemoteQuizRunner(pool, execute).run('alice', saved.execution_id) == 'failed'
    assert not db('SELECT quiz_id FROM server_remote_quizzes WHERE context_id=%s', (request.unpack()[0].context_id,))
    assert post.call_count == 1


def test_expired_lease_after_attempt_does_not_resend(pool, db, stores):
    saved, request, _, post, _, execute = setup(pool, db, stores)
    response = post.return_value
    def stop(*args, **kwargs):
        db("UPDATE server_remote_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=%s", (saved.execution_id,))
        return response
    post.side_effect = stop
    runner=RemoteQuizRunner(pool, execute)
    assert runner.run('alice', saved.execution_id) == 'failed'
    assert runner.run('alice', saved.execution_id) == 'not_claimed'
    assert post.call_count == 1
    assert not db('SELECT quiz_id FROM server_remote_quizzes WHERE context_id=%s', (request.unpack()[0].context_id,))
