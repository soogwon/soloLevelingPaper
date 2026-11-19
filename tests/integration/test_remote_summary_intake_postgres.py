"""요약의 단일 접수·동의 분리·공통 예산·취소 반환을 실제 격리 DB에서 검증한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
import json
import os
from uuid import uuid4

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_retrieval_postgres import seed
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.application.summary.remote_contract import RemoteSummaryInput, NOTICE_VERSION
from solo_leveling.interfaces.http.remote_summary_sources import PostgresSummaryPreparation
from solo_leveling.interfaces.http.remote_summary_intake import PostgresSummaryIntake
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore, RemoteExecutionConflict

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def setup(db, stores, pool):
    _, _, binding = seed(db, stores, pool, 'alice')
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES ('alice','합성 사용자') ON CONFLICT DO NOTHING")
    policy = RemoteExecutionPolicy(str(uuid4()), 120, 60, 1, 1000, 100)
    request = PostgresSummaryPreparation(pool, provider='synthetic', model='fixed', prompt_version='v1',
        language='ko', budget_policy_version=policy.version).prepare('alice', binding.context_id)
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
    return PostgresSummaryIntake(pool, settings=dict(provider='synthetic', model='fixed', prompt_version='v1',
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
    forged = RemoteSummaryInput(json.dumps(raw))
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
        submit(pool, (RemoteSummaryInput(json.dumps(raw)), *args[1:]), key)
    with pytest.raises(psycopg.errors.CheckViolation, match='summary_input_immutable'):
        db('UPDATE server_summary_inputs SET input_snapshot=input_snapshot WHERE execution_id=%s', (row.execution_id,))


def inputs(db, args):
    return db("SELECT * FROM server_summary_inputs WHERE input_snapshot->>'budget_policy_version'=%s",
              (args[-1].version,))


def executions(db, args):
    return db("SELECT * FROM server_remote_executions WHERE policy_snapshot->>'version'=%s",
              (args[-1].version,))


def test_client_model_override_and_same_version_policy_change_rejected(pool, db, stores):
    from dataclasses import replace
    args = setup(db, stores, pool)
    raw = json.loads(args[0].encoded)
    raw['model'] = 'unapproved-model'
    forged = RemoteSummaryInput(json.dumps(raw))
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


def completed_attempt(pool, args, row):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from solo_leveling.infrastructure.generation.persisted_dispatch import create_persisted_responses_transport
    from solo_leveling.infrastructure.generation.gated_responses import PreparedProviderAttempt
    intent = PostgresRemoteExecutionStore(pool).claim('alice', row.execution_id)
    post = Mock(return_value=SimpleNamespace(status_code=200,
        json=lambda: {'usage': {'input_tokens': 20, 'output_tokens': 10}}, close=Mock()))
    transport = create_persisted_responses_transport(pool, intent, args[-1].version, 'test',
        lambda *args: PreparedProviderAttempt('summary-one', args[-1], 100), post=post)
    settings = SimpleNamespace(allow_external_api=True, local_only=False, model='synthetic',
                               max_output_tokens=100, api_key='synthetic', timeout_seconds=5)
    transport.send(settings, {'model':'synthetic','max_output_tokens':100,'store':False,'input':'합성'}, 'generation')
    assert post.call_count == 1
    return intent


def test_result_restart_restore_and_foreign_access_without_new_generation(pool, db, stores):
    from solo_leveling.interfaces.http.remote_summary_results import PostgresSummaryResults
    args = setup(db, stores, pool)
    key = str(uuid4())
    row = submit(pool, args, key)
    intent = completed_attempt(pool, args, row)
    evidence = args[0].unpack()[1][0].evidence_id
    result = PostgresSummaryResults(pool).complete(intent, [dict(kind='method',text='합성 요약',evidence_ids=[evidence])])
    context = args[0].unpack()[0].context_id
    assert PostgresSummaryResults(pool).get('alice', context, key)['result'] == result
    assert PostgresSummaryResults(pool).get('bob', context, key) is None
    assert all(r['reserved_input_tokens'] == 0 for r in reserved(db, args[-1]))
    assert db('SELECT count(*) n FROM server_provider_attempts WHERE execution_id=%s', (row.execution_id,))[0]['n'] == 1


@pytest.mark.parametrize('failure', ['unsupported', 'revoked', 'cancelled', 'late_lease', 'unobserved'])
def test_failed_result_does_not_publish(pool, db, stores, failure):
    from solo_leveling.interfaces.http.remote_summary_results import PostgresSummaryResults
    from solo_leveling.interfaces.http.remote_executions import RemoteExecutionLeaseLost
    args = setup(db, stores, pool)
    key = str(uuid4())
    row = submit(pool, args, key)
    intent = (PostgresRemoteExecutionStore(pool).claim('alice', row.execution_id) if failure == 'unobserved'
              else completed_attempt(pool, args, row))
    evidence = args[0].unpack()[1][0].evidence_id
    if failure == 'revoked':
        db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s', (args[2],))
    if failure == 'cancelled':
        PostgresRemoteExecutionStore(pool).request_cancel('alice', row.execution_id)
    if failure == 'late_lease':
        db("UPDATE server_remote_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=%s", (row.execution_id,))
    with pytest.raises((ValueError, RemoteExecutionLeaseLost)):
        PostgresSummaryResults(pool).complete(intent, [dict(kind='claim', text='합성',
            evidence_ids=['foreign' if failure == 'unsupported' else evidence])])
    restored = PostgresSummaryResults(pool).get('alice', args[0].unpack()[0].context_id, key)
    assert restored['result'] is None and restored['state'] != 'completed'


def test_new_failed_summary_preserves_previous_success(pool, db, stores):
    from solo_leveling.interfaces.http.remote_summary_results import PostgresSummaryResults
    args = setup(db, stores, pool)
    db('UPDATE server_dispatch_budget_limits SET max_input_tokens=2000,max_output_tokens=1000 WHERE policy_version=%s',
       (args[-1].version,))
    first_key, second_key = str(uuid4()), str(uuid4())
    first = submit(pool, args, first_key)
    evidence = args[0].unpack()[1][0].evidence_id
    expected = PostgresSummaryResults(pool).complete(completed_attempt(pool, args, first),
        [dict(kind='method', text='이전 성공 요약', evidence_ids=[evidence])])
    second = submit(pool, args, second_key)
    with pytest.raises(ValueError):
        PostgresSummaryResults(pool).complete(completed_attempt(pool, args, second), [])
    context = args[0].unpack()[0].context_id
    assert PostgresSummaryResults(pool).get('alice', context, first_key)['result'] == expected
    assert PostgresSummaryResults(pool).get('alice', context, second_key)['result'] is None


def test_reclaimed_generation_rejects_old_completion_and_does_not_reuse_its_usage(pool, db, stores):
    from solo_leveling.interfaces.http.remote_summary_results import PostgresSummaryResults
    from solo_leveling.interfaces.http.remote_executions import RemoteExecutionLeaseLost
    args = setup(db, stores, pool)
    row = submit(pool, args)
    old = completed_attempt(pool, args, row)
    db("UPDATE server_remote_executions SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_id=%s", (row.execution_id,))
    new = PostgresRemoteExecutionStore(pool).claim('alice', row.execution_id)
    assert new.lease_token != old.lease_token
    item = [dict(kind='method', text='합성', evidence_ids=[args[0].unpack()[1][0].evidence_id])]
    with pytest.raises(RemoteExecutionLeaseLost):
        PostgresSummaryResults(pool).complete(old, item)
    with pytest.raises(ValueError, match='^remote_summary_usage_unconfirmed$'):
        PostgresSummaryResults(pool).complete(new, item)
    assert db('SELECT count(*) n FROM server_provider_attempts WHERE execution_id=%s', (row.execution_id,))[0]['n'] == 1
