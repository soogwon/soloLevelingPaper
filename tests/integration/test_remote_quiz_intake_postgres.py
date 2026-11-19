"""퀴즈의 단일 접수·동의 분리·공통 예산·취소 반환을 실제 격리 DB에서 검증한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
import json
import os
from uuid import uuid4

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from solo_leveling.interfaces.http.remote_answers import PostgresRemoteAnswerStore
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.quiz.remote_contract import RemoteQuizGenerationInput, NOTICE as NOTICE_VERSION
from solo_leveling.interfaces.http.remote_quiz_preparation import PostgresQuizPreparation
from solo_leveling.interfaces.http.remote_quiz_intake import PostgresQuizIntake
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore, RemoteExecutionConflict

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def setup(db, stores, pool):
    intent, prepared = setup_answer(db, stores, pool)
    PostgresRemoteAnswerStore(pool).save(intent, prepared)
    binding = prepared.binding
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES ('alice','합성 사용자') ON CONFLICT DO NOTHING")
    policy = RemoteExecutionPolicy(str(uuid4()), 120, 60, 1, 1000, 100)
    request = PostgresQuizPreparation(pool, settings=dict(provider='synthetic', model='fixed', prompt_version='v1',
        language='ko', budget_policy_version=policy.version), policy=policy).prepare('alice', binding.context_id,
        (prepared.evidence[0].evidence_id,), step='preview', question_count=1, question_type='multiple_choice')
    storage, ai = str(uuid4()), str(uuid4())
    for key, purpose in [(storage, 'storage'), (ai, 'ai_provider')]:
        db('''INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version)
            VALUES (%s,'alice',%s,%s,%s)''', (key, purpose, request.input_hash, NOTICE_VERSION))
    db("INSERT INTO server_dispatch_controls(control_id,generation,dispatch_enabled,intake_enabled) VALUES ('provider_dispatch',1,true,true) ON CONFLICT(control_id) DO UPDATE SET intake_enabled=true,dispatch_enabled=true")
    for kind, owner in [('owner', 'alice'), ('service', 'service')]:
        db('''INSERT INTO server_dispatch_budget_limits
            (scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
            VALUES (%s,%s,%s,'test',1000,100)''', (kind, owner, policy.version))
    return request, storage, ai, policy


def submit(pool, args, key=None, owner='alice'):
    request, storage, ai, policy = args
    return PostgresQuizIntake(pool, settings=dict(provider='synthetic', model='fixed', prompt_version='v1',
        language='ko', budget_policy_version=policy.version)).submit(owner, key or str(uuid4()), request, storage, ai,
                                             policy=policy, window_key='test')


def reserved(db, policy):
    return db('SELECT reserved_input_tokens FROM server_dispatch_budget_limits WHERE policy_version=%s',
              (policy.version,))


def test_concurrent_same_key_single_execution_and_restart(pool, db, stores):
    args = setup(db, stores, pool)
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as workers:
        rows = list(workers.map(lambda _: submit(pool, args, key), range(4)))
    assert len({r.execution_id for r in rows}) == 1
    assert len(inputs(db, args)) == 1
    assert all(r['reserved_input_tokens'] == 1000 for r in reserved(db, args[-1]))
    assert submit(pool, args, key).execution_id == rows[0].execution_id
    assert PostgresRemoteExecutionStore(pool).claim_next_question() is None
    assert db('SELECT count(*) n FROM server_provider_attempts')[0]['n'] == 0


def test_cancel_returns_unused_budget_and_replay_does_not_reserve_again(pool, db, stores):
    args = setup(db, stores, pool)
    key = str(uuid4())
    row = submit(pool, args, key)
    assert PostgresRemoteExecutionStore(pool).request_cancel('alice', row.execution_id) == 'cancel_requested'
    assert all(r['reserved_input_tokens'] == 0 for r in reserved(db, args[-1]))
    assert submit(pool, args, key).state == 'cancel_requested'
    assert all(r['reserved_input_tokens'] == 0 for r in reserved(db, args[-1]))


def test_concurrent_different_keys_cannot_overbook(pool, db, stores):
    args = setup(db, stores, pool)
    with ThreadPoolExecutor(max_workers=4) as workers:
        rows = list(workers.map(lambda _: submit(pool, args), range(4)))
    assert sum(r is not None for r in rows) == 1
    assert len(inputs(db, args)) == 1
    assert len(executions(db, args)) == 1


@pytest.mark.parametrize('change', ['owner', 'revoked', 'question_notice', 'wrong_hash', 'deleted', 'intake_stopped'])
def test_denied_admission_has_no_execution_or_reservation(pool, db, stores, change):
    args = setup(db, stores, pool)
    if change == 'revoked':
        db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s', (args[2],))
    if change == 'question_notice':
        db("UPDATE server_remote_consents SET notice_version='remote-question-v1'")
    if change == 'wrong_hash':
        db("UPDATE server_remote_consents SET input_hash='wrong'")
    if change == 'deleted':
        db("UPDATE server_papers SET deleted_at=clock_timestamp() WHERE owner_user_id='alice'")
    if change == 'intake_stopped':
        db('UPDATE server_dispatch_controls SET intake_enabled=false')
    assert submit(pool, args, owner='bob' if change == 'owner' else 'alice') is None
    assert not inputs(db, args)
    assert not executions(db, args)
    assert all(r['reserved_input_tokens'] == 0 for r in reserved(db, args[-1]))


def test_client_source_forgery_does_not_enter_queue(pool, db, stores):
    args = setup(db, stores, pool)
    raw = json.loads(args[0].encoded)
    raw['sources'][0]['original_text'] = 'Forged synthetic source.'
    forged = RemoteQuizGenerationInput(json.dumps(raw))
    db('UPDATE server_remote_consents SET input_hash=%s', (forged.input_hash,))
    assert submit(pool, (forged, *args[1:])) is None
    assert not executions(db, args)


def test_same_key_changed_input_conflicts_and_snapshot_is_immutable(pool, db, stores):
    import psycopg
    args = setup(db, stores, pool)
    key = str(uuid4())
    row = submit(pool, args, key)
    raw = json.loads(args[0].encoded)
    raw['prompt_version'] = 'other'
    with pytest.raises(RemoteExecutionConflict):
        submit(pool, (RemoteQuizGenerationInput(json.dumps(raw)), *args[1:]), key)
    with pytest.raises(psycopg.errors.CheckViolation, match='quiz_generation_input_immutable'):
        db('UPDATE server_quiz_generation_inputs SET input_snapshot=input_snapshot WHERE execution_id=%s', (row.execution_id,))


def inputs(db, args):
    return db("SELECT * FROM server_quiz_generation_inputs WHERE input_snapshot->>'budget_policy_version'=%s",
              (args[-1].version,))


def executions(db, args):
    return db("SELECT * FROM server_remote_executions WHERE policy_snapshot->>'version'=%s",
              (args[-1].version,))


def test_client_model_override_and_same_version_policy_change_rejected(pool, db, stores):
    from dataclasses import replace
    args = setup(db, stores, pool)
    raw = json.loads(args[0].encoded)
    raw['model'] = 'unapproved-model'
    forged = RemoteQuizGenerationInput(json.dumps(raw))
    db('UPDATE server_remote_consents SET input_hash=%s WHERE consent_id IN (%s,%s)',
       (forged.input_hash, args[1], args[2]))
    assert submit(pool, (forged, *args[1:])) is None
    db('UPDATE server_remote_consents SET input_hash=%s WHERE consent_id IN (%s,%s)',
       (args[0].input_hash, args[1], args[2]))
    key = str(uuid4())
    saved = submit(pool, args, key)
    with pytest.raises(RemoteExecutionConflict):
        submit(pool, (*args[:3], replace(args[3], max_input_tokens=999)), key)
    assert submit(pool, args, key).execution_id == saved.execution_id



def test_revoked_consent_blocks_new_key_but_preserves_existing_receipt(pool, db, stores):
    args = setup(db, stores, pool)
    key = str(uuid4())
    first = submit(pool, args, key)
    db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s', (args[2],))
    assert submit(pool, args, key).execution_id == first.execution_id
    assert submit(pool, args) is None
    assert len(inputs(db, args)) == 1
    assert all(r['reserved_input_tokens'] == 1000 for r in reserved(db, args[-1]))


@pytest.mark.parametrize('scope', ['owner', 'service'])
def test_missing_or_exhausted_budget_rolls_back_snapshot_and_execution(pool, db, stores, scope):
    args = setup(db, stores, pool)
    db('UPDATE server_dispatch_budget_limits SET consumed_output_tokens=max_output_tokens '
       'WHERE policy_version=%s AND scope_type=%s', (args[-1].version, scope))
    assert submit(pool, args) is None
    assert not inputs(db, args) and not executions(db, args)
    db('DELETE FROM server_dispatch_budget_limits WHERE policy_version=%s AND scope_type=%s',
       (args[-1].version, scope))
    assert submit(pool, args) is None
    assert not inputs(db, args) and not executions(db, args)
    assert all(r['reserved_input_tokens'] == 0 for r in reserved(db, args[-1]))


def test_different_contexts_share_service_budget(pool, db, stores):
    first = setup(db, stores, pool)
    # 별도 부모 행의 잠금으로 직렬화되지 않는 요청도 같은 서비스 원장을 지킨다.
    intent, prepared = setup_answer(db, stores, pool)
    PostgresRemoteAnswerStore(pool).save(intent, prepared)
    policy = first[-1]
    other = PostgresQuizPreparation(pool, settings=dict(provider='synthetic', model='fixed', prompt_version='v1',
        language='ko', budget_policy_version=policy.version), policy=policy).prepare('alice', prepared.binding.context_id,
        (prepared.evidence[0].evidence_id,), step='preview', question_count=1, question_type='multiple_choice')
    storage, ai = str(uuid4()), str(uuid4())
    for key, purpose in [(storage, 'storage'), (ai, 'ai_provider')]:
        db('INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version) '
           "VALUES (%s,'alice',%s,%s,%s)", (key, purpose, other.input_hash, NOTICE_VERSION))
    with ThreadPoolExecutor(max_workers=2) as workers:
        rows = list(workers.map(lambda args: submit(pool, args), [first, (other, storage, ai, policy)]))
    assert sum(row is not None for row in rows) == 1
    assert len(inputs(db, first)) == len(executions(db, first)) == 1


def test_replay_cannot_change_budget_window_or_policy(pool, db, stores):
    args = setup(db, stores, pool)
    key = str(uuid4())
    submit(pool, args, key)
    service = PostgresQuizIntake(pool, settings=dict(provider='synthetic', model='fixed', prompt_version='v1',
        language='ko', budget_policy_version=args[-1].version))
    with pytest.raises(RemoteExecutionConflict):
        service.submit('alice', key, *args[:3], policy=args[-1], window_key='another-window')
    assert all(r['reserved_input_tokens'] == 1000 for r in reserved(db, args[-1]))


def test_snapshot_guard_and_execution_deletion(pool, db, stores):
    import psycopg
    args = setup(db, stores, pool)
    row = submit(pool, args)
    original = db('SELECT execution_id FROM server_remote_executions WHERE operation=%s LIMIT 1', ('ask',))[0]
    with pytest.raises(psycopg.errors.CheckViolation, match='quiz_generation_input_scope_invalid'):
        db('INSERT INTO server_quiz_generation_inputs(owner_user_id,execution_id,input_snapshot) '
           "VALUES ('alice',%s,%s::jsonb)", (original['execution_id'], args[0].encoded))
    db('DELETE FROM server_remote_executions WHERE execution_id=%s', (row.execution_id,))
    assert not inputs(db, args)
