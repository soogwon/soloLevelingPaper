"""요약 큐·승인 본문 상한·영속 결과를 격리 DB와 대역 HTTP로 확인한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
import json
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores  # noqa: F401
from tests.integration.test_remote_summary_executor_postgres import setup as intake_setup
from solo_leveling.application.summary.remote_contract import RemoteSummaryInput
from solo_leveling.application.remote_execution_policy import RemoteExecutionPolicy
from solo_leveling.interfaces.http.remote_summary_results import PostgresSummaryResults
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore
from solo_leveling.infrastructure.generation.openai_generator import OpenAIGenerationSettings
from solo_leveling.infrastructure.generation.gated_responses import payload_digest
from solo_leveling.workers.remote_summary import summary_payload
from solo_leveling.workers.summary_runtime import load_plan, create_runner

pytestmark = pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def setup(pool, db, stores, tmp_path, response='ok'):
    saved,key,context,post,_,ai = intake_setup(db,stores,pool,response=response)
    row = db('SELECT policy_snapshot FROM server_remote_executions WHERE execution_id=%s',(saved.execution_id,))[0]
    policy = RemoteExecutionPolicy(**row['policy_snapshot'])
    raw = db('SELECT input_snapshot FROM server_summary_inputs WHERE execution_id=%s',(saved.execution_id,))[0]
    request = RemoteSummaryInput(json.dumps(raw['input_snapshot']))
    generation = dict(model='synthetic',timeout_seconds=30,max_output_tokens=100)
    payload = summary_payload(request,OpenAIGenerationSettings('synthetic',**generation),policy)
    config = dict(schema_version=1,database_name='postgres',schema_name='test',database_role='summary_worker',
        policy=policy.snapshot(),generation=generation,window_key='test',prompt_version='remote-summary-v1',
        requests=dict(max_requests=1,max_input_tokens=1000,max_output_tokens=100,payloads={payload_digest(payload):200}))
    path=tmp_path/'summary.json';path.write_text(json.dumps(config))
    return saved,key,context,ai,load_plan(path),post,payload


def test_concurrent_queue_persists_once_and_recovers_after_restart(pool,db,stores,tmp_path):
    saved,key,context,_,plan,post,payload=setup(pool,db,stores,tmp_path)
    with ThreadPoolExecutor(max_workers=3) as workers:
        result=list(workers.map(lambda _:create_runner(pool,plan,'synthetic',post=post).run_once(),range(3)))
    assert result.count('finished')==1 and result.count(None)==2
    assert post.call_count==1 and post.call_args.kwargs['json']==payload
    result=PostgresSummaryResults(pool).get('alice',context,key)
    assert result['state']=='completed' and result['result']['items'][0]['text']=='합성 요약'
    assert PostgresSummaryResults(pool).get('bob',context,key) is None
    assert create_runner(pool,plan,'synthetic',post=post).run_once() is None
    assert post.call_count==1


@pytest.mark.parametrize('failure',['unapproved_payload','foreign','empty','timeout','revoked','cancelled','other_policy','other_window'])
def test_queue_boundaries_do_not_publish_or_retry(pool,db,stores,tmp_path,failure):
    saved,key,context,ai,plan,post,_=setup(pool,db,stores,tmp_path,
        response=failure if failure in ('foreign','empty','timeout') else 'ok')
    if failure=='unapproved_payload':plan.raw['requests']['payloads']={'0'*64:200}
    if failure=='revoked':db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s',(ai,))
    if failure=='cancelled':PostgresRemoteExecutionStore(pool).request_cancel('alice',saved.execution_id)
    runner=create_runner(pool,plan,'synthetic',post=post)
    if failure=='other_policy':runner.policy='different'
    if failure=='other_window':runner.window='different'
    status=runner.run_once()
    assert status is None if failure in ('other_policy','other_window') else status!='finished'
    assert post.call_count==(1 if failure in ('foreign','empty','timeout') else 0)
    assert PostgresSummaryResults(pool).get('alice',context,key)['result'] is None
    assert runner.run_once() is None
