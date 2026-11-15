"""실제 실행·원장과 B 물리 전송 gate를 연결한다. HTTP는 항상 합성 대역이다."""
# ruff: noqa: F811
from types import SimpleNamespace as NS
from unittest.mock import Mock
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores, new_id  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.infrastructure.generation.gated_responses import PreparedProviderAttempt
from solo_leveling.infrastructure.generation.persisted_dispatch import create_persisted_responses_transport

pytestmark=pytest.mark.skipif(not os.environ.get('SOLO_TEST_POSTGRES_DSN'),reason='실제 PostgreSQL DSN 필요')
SETTINGS=NS(allow_external_api=True,local_only=False,model='synthetic',max_output_tokens=20,
            api_key='synthetic-not-a-key',timeout_seconds=1)
PAYLOAD={'model':'synthetic','store':False,'max_output_tokens':20,'input':'합성 입력'}


def fixture(db,stores,pool):
    intent,_=setup_answer(db,stores,pool)
    policy=new_id()
    db("""INSERT INTO server_dispatch_controls(control_id,generation,dispatch_enabled)
          VALUES ('provider_dispatch',1,true) ON CONFLICT(control_id) DO UPDATE SET dispatch_enabled=true""")
    for scope,scope_id in [('service','service'),('owner','alice')]:
        db('''INSERT INTO server_dispatch_budget_limits
            (scope_type,scope_id,policy_version,window_key,max_input_tokens,max_output_tokens)
            VALUES (%s,%s,%s,'window',1000,1000)''',(scope,scope_id,policy))
    post=Mock(return_value=NS(status_code=200,json=lambda:{'output':[], 'usage':{'input_tokens':8,'output_tokens':4}},close=Mock()))
    def build(key, budget=None):
        return create_persisted_responses_transport(pool,intent,policy,'window',
            lambda stage,payload,digest:PreparedProviderAttempt(key,digest,100),post=post,execution_budget=budget)
    return intent,post,build


@pytest.mark.parametrize('stage',['generation','repair','semantic_verification','coverage','translation'])
def test_each_stage_reserves_settles_and_rejects_replay(db,stores,pool,stage):
    intent,post,build=fixture(db,stores,pool)
    assert build(stage).send(SETTINGS,PAYLOAD,stage)['usage']['input_tokens']==8
    with pytest.raises(DispatchError,match='DISPATCH_NOT_AUTHORIZED'):
        build(stage).send(SETTINGS,PAYLOAD,stage)
    assert post.call_count==1
    row=db('SELECT status,stage,actual_input_tokens FROM server_provider_attempts WHERE execution_id=%s',(intent.execution_id,))[0]
    assert row=={'status':'observed','stage':stage,'actual_input_tokens':8}


@pytest.mark.parametrize('failure',['timeout','settlement'])
def test_unknown_survives_new_transport_and_blocks_new_key(db,stores,pool,failure):
    intent,post,build=fixture(db,stores,pool)
    transport=build('first')
    if failure=='timeout': post.side_effect=TimeoutError('private-provider-text')
    else: transport.dispatch.ledger.observe=Mock(side_effect=RuntimeError('private-ledger-text'))
    with pytest.raises(DispatchError): transport.send(SETTINGS,PAYLOAD,'generation')
    with pytest.raises(DispatchError,match='DISPATCH_NOT_AUTHORIZED'):
        build('new-key').send(SETTINGS,PAYLOAD,'semantic_verification')
    assert post.call_count==1
    assert db('SELECT status FROM server_provider_attempts WHERE execution_id=%s',(intent.execution_id,))==[{'status':'unknown'}]


def test_revocation_between_stages_blocks_following_provider_call(db,stores,pool):
    intent,post,build=fixture(db,stores,pool)
    build('generation').send(SETTINGS,PAYLOAD,'generation')
    db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE consent_id=%s',(intent.ai_consent_id,))
    with pytest.raises(DispatchError,match='DISPATCH_NOT_AUTHORIZED'):
        build('semantic').send(SETTINGS,PAYLOAD,'semantic_verification')
    assert post.call_count==1
