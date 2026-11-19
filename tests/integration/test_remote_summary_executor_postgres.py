"""외부 API 없이 실제 원장과 대역 전송으로 명시적 요약 1건을 실행한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import json
import os
from uuid import uuid4

import pytest

from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_retrieval_postgres import seed
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.interfaces.http.remote_summary_sources import PostgresSummaryPreparation
from solo_leveling.interfaces.http.remote_summary_intake import PostgresSummaryIntake
from solo_leveling.interfaces.http.remote_summary_results import PostgresSummaryResults
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
from solo_leveling.workers.remote_summary import PersistedSummaryExecutor, RemoteSummaryRunner

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def setup(db, stores, pool, *, response='ok'):
    _, _, binding = seed(db, stores, pool, 'alice')
    db("INSERT INTO server_users(owner_user_id,display_name) VALUES ('alice','합성 사용자') ON CONFLICT DO NOTHING")
    policy = RemoteExecutionPolicy(str(uuid4()), 120, 60, 1, 1000, 100)
    config = dict(provider='openai', model='synthetic', prompt_version='remote-summary-v1',
                  language='ko', budget_policy_version=policy.version)
    request = PostgresSummaryPreparation(pool, **config).prepare('alice', binding.context_id)
    storage, ai, key = str(uuid4()), str(uuid4()), str(uuid4())
    for cid, purpose in ((storage, 'storage'), (ai, 'ai_provider')):
        db("""INSERT INTO server_remote_consents(consent_id,owner_user_id,purpose,input_hash,notice_version)
            VALUES (%s,'alice',%s,%s,'remote-summary-v1')""", (cid, purpose, request.input_hash))
    db("""INSERT INTO server_dispatch_controls(control_id,generation,dispatch_enabled,intake_enabled)
        VALUES ('provider_dispatch',1,true,true) ON CONFLICT(control_id) DO UPDATE
        SET dispatch_enabled=true,intake_enabled=true""")
    for kind, owner in [('owner','alice'),('service','service')]:
        db("""INSERT INTO server_dispatch_budget_limits(scope_type,scope_id,policy_version,window_key,
            max_input_tokens,max_output_tokens) VALUES (%s,%s,%s,'test',1000,100)""", (kind,owner,policy.version))
    saved = PostgresSummaryIntake(pool,settings=config).submit('alice',key,request,storage,ai,
        policy=policy,window_key='test')
    evidence = request.unpack()[1][0].evidence_id
    content = json.dumps({'items': [] if response == 'empty' else [dict(kind='method',text='합성 요약',
        evidence_ids=['foreign' if response == 'foreign' else evidence])]})
    envelope = dict(status='completed', output=[dict(type='message',role='assistant',status='completed',
        content=[dict(type='output_text',text=content)])], usage=dict(input_tokens=20,output_tokens=10))
    post = Mock(side_effect=TimeoutError('synthetic') if response == 'timeout' else None,
                return_value=SimpleNamespace(status_code=200,json=lambda:envelope,close=Mock()))
    settings = OpenAIGenerationSettings('synthetic',model='synthetic',max_output_tokens=100,allow_external_api=True)
    executor = PersistedSummaryExecutor(pool,settings=settings,token_upper_bound=lambda *_:100,
                                       window_key='test',post=post)
    return saved, key, binding.context_id, post, executor, ai


def test_concurrent_run_calls_provider_once_and_restores_same_result(pool, db, stores):
    row, key, context, post, executor, _ = setup(db,stores,pool)
    runner = RemoteSummaryRunner(pool,executor)
    with ThreadPoolExecutor(max_workers=2) as workers:
        statuses = list(workers.map(lambda _:runner.run('alice',row.execution_id),range(2)))
    assert sorted(statuses) == ['finished','not_claimed']
    assert post.call_count == 1
    payload = post.call_args.kwargs['json']
    assert payload['store'] is False and payload['model'] == 'synthetic'
    restored = PostgresSummaryResults(pool).get('alice',context,key)
    assert restored['result']['items'][0]['text'] == '합성 요약'
    assert runner.run('alice',row.execution_id) == 'not_claimed'
    assert post.call_count == 1
    assert runner.run('bob',row.execution_id) == 'not_found'


@pytest.mark.parametrize('response',['empty','foreign','timeout'])
def test_failed_or_insufficient_result_is_not_retried(pool, db, stores, response):
    row,key,context,post,executor,_ = setup(db,stores,pool,response=response)
    runner=RemoteSummaryRunner(pool,executor)
    assert runner.run('alice',row.execution_id) == 'failed'
    assert runner.run('alice',row.execution_id) == 'not_claimed'
    assert post.call_count == 1
    assert PostgresSummaryResults(pool).get('alice',context,key)['result'] is None


def test_withdrawn_consent_never_sends(pool, db, stores):
    row,_,_,post,executor,ai = setup(db,stores,pool)
    db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s',(ai,))
    assert RemoteSummaryRunner(pool,executor).run('alice',row.execution_id) == 'blocked'
    post.assert_not_called()
    assert db('SELECT state FROM server_remote_executions WHERE execution_id=%s',(row.execution_id,))[0]['state'] == 'failed'
