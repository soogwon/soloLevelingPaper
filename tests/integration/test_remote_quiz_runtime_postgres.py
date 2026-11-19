"""실제 격리 DB 큐와 운영 조립체를 합성 provider로 검증한다."""
# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
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
from solo_leveling.infrastructure.generation.gated_responses import payload_digest
from solo_leveling.workers.remote_quiz import quiz_payload
from solo_leveling.workers.quiz_runtime import load_plan, create_runner
from solo_leveling.workers.quiz_validation import PREFIX, CRITERIA, MARKER, PROFILE

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'), reason='격리 PostgreSQL 필요')


def setup(pool, db, stores, tmp_path):
    request,storage,ai,policy=intake_setup(db,stores,pool)
    raw=json.loads(request.encoded);raw.update(provider='openai',prompt_version=PROFILE)
    request=RemoteQuizGenerationInput(json.dumps(raw))
    db('UPDATE server_remote_consents SET input_hash=%s WHERE consent_id=ANY(%s)',(request.input_hash,[storage,ai]))
    settings=dict(provider='openai',model='fixed',prompt_version=PROFILE,language='ko',budget_policy_version=policy.version)
    saved=PostgresQuizIntake(pool,settings=settings).submit('alice',str(uuid4()),request,storage,ai,policy=policy,window_key='test')
    generation=dict(model='fixed',timeout_seconds=30,max_output_tokens=100)
    payload=quiz_payload(request,OpenAIGenerationSettings('synthetic',**generation),policy)
    config=dict(schema_version=1,database_name='postgres',schema_name='test',database_role='quiz_worker',
        policy=policy.snapshot(),generation=generation,window_key='test',validation_profile=PROFILE,
        requests=dict(max_requests=1,max_input_tokens=1000,max_output_tokens=100,payloads={payload_digest(payload):200}))
    path=tmp_path/'quiz.json';path.write_text(json.dumps(config))
    plan=load_plan(path)
    source=request.unpack()[1][0];text=source.original_text;answer=text[:3]
    item=dict(question_type='multiple_choice',prompt=PREFIX+text.replace(answer,MARKER,1),
        choices=[answer,'불일치'],correct_index=0,answer=answer,grading_criteria=CRITERIA,evidence_ids=[source.evidence_id])
    envelope=dict(status='completed',output=[dict(type='message',role='assistant',status='completed',
        content=[dict(type='output_text',text=json.dumps(dict(items=[item])))])],usage=dict(input_tokens=20,output_tokens=10))
    post=Mock(return_value=SimpleNamespace(status_code=200,json=lambda:envelope,close=Mock()))
    return saved,request,ai,plan,post,envelope


def test_concurrent_queue_uses_real_validator_and_one_dispatch(pool,db,stores,tmp_path):
    saved,request,_,plan,post,_=setup(pool,db,stores,tmp_path)
    with ThreadPoolExecutor(max_workers=3) as workers:
        result=list(workers.map(lambda _:create_runner(pool,plan,'synthetic',post=post).run_once(),range(3)))
    assert result.count('finished')==1 and result.count(None)==2
    assert post.call_count==1
    restored=PostgresRemoteQuiz(pool).generation_result('alice',request.unpack()[0].context_id,saved.lookup.request_key)
    assert restored['state']=='completed' and restored['quiz'] is not None
    assert create_runner(pool,plan,'synthetic',post=post).run_once() is None
    assert post.call_count==1


@pytest.mark.parametrize('failure',['unapproved_payload','invalid_answer','revoked','cancelled','other_policy','other_window'])
def test_queue_boundaries(pool,db,stores,tmp_path,failure):
    saved,request,ai,plan,post,envelope=setup(pool,db,stores,tmp_path)
    if failure=='unapproved_payload':
        plan.raw['requests']['payloads']={'0'*64:200}
    if failure=='invalid_answer':
        item=json.loads(envelope['output'][0]['content'][0]['text'])['items'][0]
        item.update(answer='거짓',choices=['거짓','불일치'])
        envelope['output'][0]['content'][0]['text']=json.dumps(dict(items=[item]))
    if failure=='revoked':
        db('UPDATE server_remote_consents SET revoked_at=clock_timestamp() WHERE consent_id=%s',(ai,))
    if failure=='cancelled':
        PostgresRemoteExecutionStore(pool).request_cancel('alice',saved.execution_id)
    runner=create_runner(pool,plan,'synthetic',post=post)
    if failure=='other_policy':runner.policy='different'
    if failure=='other_window':runner.window='different'
    status=runner.run_once()
    assert status is None if failure in ('other_policy','other_window') else status!='finished'
    assert post.call_count==(1 if failure=='invalid_answer' else 0)
    assert not db('SELECT quiz_id FROM server_remote_quizzes WHERE context_id=%s',(request.unpack()[0].context_id,))
