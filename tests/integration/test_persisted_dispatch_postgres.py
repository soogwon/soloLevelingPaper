"""실제 실행·원장과 B 물리 전송 gate를 연결한다. HTTP는 항상 합성 대역이다."""
# ruff: noqa: F811
from types import SimpleNamespace as NS
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import os
import pytest
from tests.integration.test_remote_processing_postgres import pool, db, stores, new_id  # noqa: F401
from tests.integration.test_remote_answer_store_postgres import setup_answer
from solo_leveling.application.provider_dispatch import DispatchError
from solo_leveling.infrastructure.generation.gated_responses import PreparedProviderAttempt
from solo_leveling.infrastructure.generation.persisted_dispatch import create_persisted_responses_transport
from solo_leveling.interfaces.http.remote_executions import PostgresRemoteExecutionStore


@pytest.fixture(autouse=True)
def forbid_provider_network(monkeypatch):
    # PostgreSQL 접속은 허용하되 주입을 빠뜨린 실제 provider 전송은 즉시 실패시킨다.
    import requests
    monkeypatch.setattr(requests, 'post', Mock(side_effect=AssertionError('provider network forbidden')))

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


@pytest.mark.parametrize('failure', [
    'timeout', 'settlement', 'http-429', 'http-500', 'json-error', 'envelope-list',
    'missing-usage', 'usage-list', 'missing-input', 'missing-output', 'bool-input',
    'negative-output', 'float-input', 'response-close',
])
def test_unknown_survives_new_transport_and_blocks_new_key(db,stores,pool,failure):
    intent,post,build=fixture(db,stores,pool)
    original_execution = db('SELECT * FROM server_remote_executions WHERE execution_id=%s', (intent.execution_id,))
    transport=build('first')
    response = post.return_value
    response.json = Mock(return_value={'output': [], 'usage': {'input_tokens': 8, 'output_tokens': 4}})
    if failure == 'timeout':
        post.side_effect = TimeoutError('private-provider-text')
    elif failure == 'settlement':
        transport.dispatch.ledger.observe = Mock(side_effect=RuntimeError('private-ledger-text'))
    elif failure.startswith('http-'):
        response.status_code = int(failure.split('-')[1])
    elif failure == 'json-error':
        response.json.side_effect = ValueError('private-response-text')
    elif failure == 'response-close':
        response.close.side_effect = RuntimeError('private-close-text')
    elif failure == 'envelope-list':
        response.json.return_value = ['private-response-text']
    elif failure == 'missing-usage':
        response.json.return_value = {'output': [], 'private': 'private-response-text'}
    elif failure == 'usage-list':
        response.json.return_value['usage'] = []
    else:
        usage = response.json.return_value['usage']
        if failure == 'missing-input': del usage['input_tokens']
        elif failure == 'missing-output': del usage['output_tokens']
        elif failure == 'bool-input': usage['input_tokens'] = True
        elif failure == 'negative-output': usage['output_tokens'] = -1
        elif failure == 'float-input': usage['input_tokens'] = 8.5
        else: pytest.fail('unknown fixture mode')
    expected = 'DISPATCH_RECONCILIATION_UNAVAILABLE' if failure == 'settlement' else 'DISPATCH_USAGE_UNKNOWN'
    with pytest.raises(DispatchError, match='^' + expected + '$'):
        transport.send(SETTINGS,PAYLOAD,'generation')
    assert response.close.call_count == (0 if failure == 'timeout' else 1)
    if failure.startswith('http-'):
        response.json.assert_not_called()

    # 실패한 실제 전송 1건은 예약을 환급하거나 성공 사용량으로 추정하지 않는다.
    attempts, limits, execution = ledger_snapshot(db, intent)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt['status'] == 'unknown'
    assert attempt['dispatch_key'] == 'first' and attempt['stage'] == 'generation'
    assert (attempt['reserved_input_tokens'], attempt['reserved_output_tokens']) == (100, 20)
    assert attempt['actual_input_tokens'] is None and attempt['actual_output_tokens'] is None
    assert_balances(limits, reserved=(100, 20), consumed=(0, 0))
    assert execution == original_execution
    assert execution[0]['state'] == 'running' and execution[0]['payload_json'] is None

    # 상태 조회는 unknown을 알릴 뿐 종결·환급·추가 예약을 수행하지 않는다.
    stored = PostgresRemoteExecutionStore(pool).lookup(intent.owner_user_id, 'ask', intent.execution_id)
    assert stored.state == 'running' and stored.budget_state == 'usage_unknown'
    assert stored.result_available is False and stored.payload_json is None
    assert ledger_snapshot(db, intent) == (attempts, limits, execution)

    # 같은 transport의 메모리 차단만으로 영속 차단을 증명하지 않는다.
    with pytest.raises(DispatchError, match='^DISPATCH_BLOCKED$'):
        transport.send(SETTINGS, PAYLOAD, 'generation')
    for key in ('first', 'new-key'):
        with pytest.raises(DispatchError, match='^DISPATCH_NOT_AUTHORIZED$'):
            build(key).send(SETTINGS, PAYLOAD, 'semantic_verification')
        assert ledger_snapshot(db, intent) == (attempts, limits, execution)
    assert post.call_count==1


def ledger_snapshot(db, intent):
    attempts = db('SELECT * FROM server_provider_attempts WHERE execution_id=%s ORDER BY attempt_id',
                  (intent.execution_id,))
    limits = db('''SELECT * FROM server_dispatch_budget_limits WHERE policy_version=(
                       SELECT policy_version FROM server_provider_attempts WHERE execution_id=%s LIMIT 1)
                   ORDER BY scope_type,scope_id''', (intent.execution_id,))
    execution = db('SELECT * FROM server_remote_executions WHERE execution_id=%s', (intent.execution_id,))
    return attempts, limits, execution


def assert_balances(limits, *, reserved, consumed):
    # 빈 SELECT 결과나 한쪽 scope만 읽고 루프가 통과하는 거짓 양성을 막는다.
    assert len(limits) == 2
    assert {(row['scope_type'], row['scope_id']) for row in limits} == {
        ('owner', 'alice'), ('service', 'service')}
    for row in limits:
        assert (row['reserved_input_tokens'], row['reserved_output_tokens']) == reserved
        assert (row['consumed_input_tokens'], row['consumed_output_tokens']) == consumed


@pytest.mark.parametrize('usage', [(8, 4), (0, 0), (101, 4), (8, 21)],
                         ids=['normal', 'zero', 'input-overrun', 'output-overrun'])
def test_late_usage_is_idempotent_and_does_not_resend_old_dispatch(db, stores, pool, usage):
    intent, post, build = fixture(db, stores, pool)
    response = post.return_value
    response.json = Mock(return_value={'output': []})
    with pytest.raises(DispatchError, match='^DISPATCH_USAGE_UNKNOWN$'):
        build('first').send(SETTINGS, PAYLOAD, 'generation')
    unknown, reserved, original_execution = ledger_snapshot(db, intent)
    assert len(unknown) == 1 and unknown[0]['status'] == 'unknown'
    assert_balances(reserved, reserved=(100, 20), consumed=(0, 0))
    attempt_id = str(unknown[0]['attempt_id'])

    # 신뢰하는 늦은 사용량만 새 원장 adapter로 정산하며 HTTP를 시작하지 않는다.
    ledger = build('unused').dispatch.ledger
    ledger.observe(attempt_id, *usage)
    settled = ledger_snapshot(db, intent)
    assert len(settled[0]) == 1
    assert settled[0][0]['attempt_id'] == unknown[0]['attempt_id']
    assert (settled[0][0]['actual_input_tokens'], settled[0][0]['actual_output_tokens']) == usage
    ledger.observe(attempt_id, *usage)
    ledger.unknown(attempt_id)
    assert ledger_snapshot(db, intent) == settled
    with pytest.raises(ValueError, match='^dispatch_usage_conflict$'):
        ledger.observe(attempt_id, usage[0] + 1, usage[1])
    assert ledger_snapshot(db, intent) == settled
    assert settled[0][0]['status'] == 'observed'
    stored = PostgresRemoteExecutionStore(pool).lookup(intent.owner_user_id, 'ask', intent.execution_id)
    assert stored.state == 'running'
    if usage[0] <= 100 and usage[1] <= 20:
        assert stored.budget_state == 'available'
    assert stored.result_available is False
    assert_balances(settled[1], reserved=(0, 0), consumed=usage)
    assert settled[2] == original_execution
    with pytest.raises(DispatchError, match='^DISPATCH_NOT_AUTHORIZED$'):
        build('first').send(SETTINGS, PAYLOAD, 'generation')
    assert ledger_snapshot(db, intent) == settled
    assert post.call_count == 1

    if usage[0] > 100 or usage[1] > 20:
        # usage가 알려져도 예약 초과는 새 전송 키로 우회할 수 없다.
        with pytest.raises(DispatchError, match='^DISPATCH_NOT_AUTHORIZED$'):
            build('semantic').send(SETTINGS, PAYLOAD, 'semantic_verification')
        assert ledger_snapshot(db, intent) == settled
        assert post.call_count == 1
        return

    # 정상 후속 단계는 명시적인 별도 key로 새 허가를 받아야 시작한다.
    response.json.return_value = {'output': [], 'usage': {'input_tokens': 8, 'output_tokens': 4}}
    assert build('semantic').send(SETTINGS, PAYLOAD, 'semantic_verification')['usage']['input_tokens'] == 8
    assert post.call_count == 2
    attempts, limits, execution = ledger_snapshot(db, intent)
    assert len(attempts) == 2 and all(row['status'] == 'observed' for row in attempts)
    assert_balances(limits, reserved=(0, 0), consumed=(usage[0] + 8, usage[1] + 4))
    assert execution == settled[2]


def test_inflight_reservation_blocks_same_and_new_key_without_second_http(db, stores, pool):
    intent, post, build = fixture(db, stores, pool)
    entered, release = Event(), Event()
    response = post.return_value
    response.json = Mock(return_value={'output': []})

    def pending_http(*_args, **_kwargs):
        entered.set()
        if not release.wait(10):
            raise TimeoutError('synthetic test release missing')
        return response

    post.side_effect = pending_http
    with ThreadPoolExecutor(max_workers=1) as worker:
        first = worker.submit(build('first').send, SETTINGS, PAYLOAD, 'generation')
        try:
            assert entered.wait(10), '합성 HTTP에 도달하지 못함'
            snapshot = ledger_snapshot(db, intent)
            assert len(snapshot[0]) == 1 and snapshot[0][0]['status'] == 'reserved'
            assert_balances(snapshot[1], reserved=(100, 20), consumed=(0, 0))
            for key in ('first', 'new-key'):
                with pytest.raises(DispatchError, match='^DISPATCH_NOT_AUTHORIZED$'):
                    build(key).send(SETTINGS, PAYLOAD, 'semantic_verification')
                assert ledger_snapshot(db, intent) == snapshot
            assert post.call_count == 1
        finally:
            release.set()
        with pytest.raises(DispatchError, match='^DISPATCH_USAGE_UNKNOWN$'):
            first.result(timeout=10)
    attempts, limits, execution = ledger_snapshot(db, intent)
    assert len(attempts) == 1 and attempts[0]['status'] == 'unknown'
    assert attempts[0]['attempt_id'] == snapshot[0][0]['attempt_id']
    assert_balances(limits, reserved=(100, 20), consumed=(0, 0))
    assert execution == snapshot[2] and post.call_count == 1
    assert response.close.call_count == 1


def test_committed_usage_with_lost_ack_is_not_downgraded_or_counted_twice(db, stores, pool):
    intent, post, build = fixture(db, stores, pool)
    transport = build('first')
    observe = transport.dispatch.ledger.observe

    def committed_then_lost_ack(*args):
        observe(*args)
        raise RuntimeError('synthetic settlement acknowledgement lost')

    transport.dispatch.ledger.observe = committed_then_lost_ack
    with pytest.raises(DispatchError, match='^DISPATCH_RECONCILIATION_UNAVAILABLE$'):
        transport.send(SETTINGS, PAYLOAD, 'generation')
    settled = ledger_snapshot(db, intent)
    assert len(settled[0]) == 1 and settled[0][0]['status'] == 'observed'
    assert (settled[0][0]['actual_input_tokens'], settled[0][0]['actual_output_tokens']) == (8, 4)
    assert_balances(settled[1], reserved=(0, 0), consumed=(8, 4))
    assert post.call_count == 1

    fresh_ledger = build('unused').dispatch.ledger
    fresh_ledger.observe(str(settled[0][0]['attempt_id']), 8, 4)
    fresh_ledger.unknown(str(settled[0][0]['attempt_id']))
    stored = PostgresRemoteExecutionStore(pool).lookup(intent.owner_user_id, 'ask', intent.execution_id)
    assert stored.budget_state == 'available' and stored.state == 'running'
    assert stored.result_available is False
    with pytest.raises(DispatchError, match='^DISPATCH_NOT_AUTHORIZED$'):
        build('first').send(SETTINGS, PAYLOAD, 'generation')
    assert ledger_snapshot(db, intent) == settled
    assert post.call_count == 1


def test_revocation_between_stages_blocks_following_provider_call(db,stores,pool):
    intent,post,build=fixture(db,stores,pool)
    build('generation').send(SETTINGS,PAYLOAD,'generation')
    db('UPDATE server_remote_consents SET revoked_at=CURRENT_TIMESTAMP WHERE consent_id=%s',(intent.ai_consent_id,))
    with pytest.raises(DispatchError,match='DISPATCH_NOT_AUTHORIZED'):
        build('semantic').send(SETTINGS,PAYLOAD,'semantic_verification')
    assert post.call_count==1
