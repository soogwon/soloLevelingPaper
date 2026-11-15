from dataclasses import replace
import json

import pytest

from solo_leveling.application.evidence_qa.remote_contract import RemoteSearchBinding, RemoteSearchFilters
from solo_leveling.application.evidence_qa.remote_input import question_input_hash, CONFIG_KEYS
from solo_leveling.application.provider_dispatch import (
    DispatchIntent, DispatchError, ProviderDispatch, ProviderResponse, AttemptPermit,
)


@pytest.fixture
def inputs():
    binding = RemoteSearchBinding('alice', 'ctx', 'paper', 'v', 'pub', 'parse', 'tr', 'set', '{"dimension":2}')
    return binding, '원문 질문\r\n', RemoteSearchFilters((7, 6), ('b', 'a')), 5, {k: 'v1' for k in CONFIG_KEYS}


def test_hash_normalizes_filter_order_not_question_or_defaults(inputs):
    b, q, f, k, config = inputs
    result = question_input_hash(*inputs)
    assert result.startswith('remote-question-v1:')
    assert result == question_input_hash(b, q, RemoteSearchFilters((6, 7), ('a', 'b'), False), k, config)
    assert result != question_input_hash(b, q.strip(), f, k, config)
    assert result != question_input_hash(b, q, replace(f, strict_pages=True), k, config)
    assert result != question_input_hash(b, q.replace('\r\n', '\n'), f, k, config)


@pytest.mark.parametrize('field', ['owner_user_id', 'context_id', 'publication_id', 'parse_revision_id',
                                  'translation_revision_id', 'embedding_set_id'])
def test_hash_binds_fixed_scope(inputs, field):
    b, *rest = inputs
    assert question_input_hash(*inputs) != question_input_hash(replace(b, **{field: 'other'}), *rest)


@pytest.mark.parametrize('key', sorted(CONFIG_KEYS))
def test_hash_binds_processing_policy(inputs, key):
    b, q, f, k, config = inputs
    assert question_input_hash(*inputs) != question_input_hash(b, q, f, k, {**config, key: 'other'})


def test_unknown_config_and_nonfinite_fingerprint_are_rejected(inputs):
    from solo_leveling.application.evidence_qa.remote_contract import RemoteRetrievalContractError
    b, q, f, k, config = inputs
    for altered in [{**config, 'api_key': 'never-hash'}, {}]:
        with pytest.raises(RemoteRetrievalContractError, match='REMOTE_INPUT_INVALID'):
            question_input_hash(b, q, f, k, altered)
    with pytest.raises(RemoteRetrievalContractError, match='REMOTE_INPUT_INVALID'):
        question_input_hash(replace(b, model_fingerprint='{"value":NaN}'), q, f, k, config)


class Ledger:
    """A 영속 원장 대신 사용한 대역. 이 테스트가 실제 DB 원자성을 검증하지는 않는다."""
    def __init__(self):
        self.requests, self.observed, self.unknowns, self.keys = [], [], [], set()
        self.allowed, self.fail = True, False

    def authorize(self, request):
        if self.fail: raise RuntimeError('private-ledger-details')
        self.requests.append(request)
        if not self.allowed or self.unknowns or request.dispatch_key in self.keys: return None
        self.keys.add(request.dispatch_key)
        return AttemptPermit(str(len(self.requests)), request)

    def observe(self, *usage): self.observed.append(usage)
    def unknown(self, key): self.unknowns.append(key)


@pytest.fixture
def dispatch():
    intent = DispatchIntent('alice', 'exec', 'lease', 'hash', 'storage', 'ai', 'notice')
    ledger = Ledger()
    return ProviderDispatch(ledger, intent), ledger


def send(gate, key, transport=lambda _: ProviderResponse({'synthetic': True}, 8, 4), stage='generation'):
    return gate.send(stage, 10, 10, transport, dispatch_key=key)


def test_each_stage_uses_same_intent_and_rechecks_authorization(dispatch):
    gate, ledger = dispatch
    assert send(gate, 'generation').payload == {'synthetic': True}
    ledger.allowed = False  # 조회 이후 동의 철회/한도 소진을 대역으로 주입
    calls = []
    with pytest.raises(DispatchError, match='DISPATCH_NOT_AUTHORIZED'):
        send(gate, 'verify', lambda _: calls.append(1), 'semantic_verification')
    assert calls == [] and len(ledger.observed) == 1
    assert ledger.requests[0].intent == ledger.requests[1].intent


@pytest.mark.parametrize('mode', ['timeout', 'missing-usage', 'bad-usage'])
def test_uncertain_attempt_is_held_and_blocks_further_transmission(dispatch, mode):
    gate, ledger = dispatch
    calls = []
    def transport(_):
        calls.append(1)
        if mode == 'timeout': raise TimeoutError('private-provider-details')
        return ProviderResponse({}, None if mode == 'missing-usage' else True, 2)
    with pytest.raises(DispatchError, match='^DISPATCH_USAGE_UNKNOWN$'):
        send(gate, 'one', transport)
    with pytest.raises(DispatchError, match='DISPATCH_BLOCKED'):
        send(gate, 'two', transport)
    assert calls == [1] and ledger.unknowns == ['1'] and ledger.observed == []


def test_authorization_failure_blocks_without_provider_or_automatic_retry(dispatch):
    gate, ledger = dispatch
    ledger.fail = True
    calls = []
    with pytest.raises(DispatchError, match='^DISPATCH_AUTHORIZATION_UNAVAILABLE$'):
        send(gate, 'one', lambda _: calls.append(1))
    with pytest.raises(DispatchError, match='DISPATCH_BLOCKED'):
        send(gate, 'one', lambda _: calls.append(1))
    assert calls == []


def test_invalid_permit_blocks_later_authorization(dispatch):
    gate, ledger = dispatch
    calls = []
    ledger.authorize = lambda request: AttemptPermit('', request)
    with pytest.raises(DispatchError, match='DISPATCH_PERMIT_INVALID'):
        send(gate, 'one', lambda _: calls.append(1))
    def forbidden(_):
        pytest.fail('불명확한 허가 뒤에는 원장과 전송을 다시 호출하면 안 됩니다')
    ledger.authorize = forbidden
    with pytest.raises(DispatchError, match='DISPATCH_BLOCKED'):
        send(gate, 'two', forbidden)
    assert calls == []


def test_restarting_controller_does_not_reuse_consumed_dispatch_key(dispatch):
    gate, ledger = dispatch
    send(gate, 'stable-key')
    fresh = ProviderDispatch(ledger, gate.intent)
    calls = []
    with pytest.raises(DispatchError, match='DISPATCH_NOT_AUTHORIZED'):
        send(fresh, 'stable-key', lambda _: calls.append(1))
    assert calls == [] and len(ledger.observed) == 1


def test_known_overrun_is_reported_as_observed_and_stops_next_call(dispatch):
    gate, ledger = dispatch
    with pytest.raises(DispatchError, match='DISPATCH_RESERVATION_EXCEEDED'):
        send(gate, 'one', lambda _: ProviderResponse({}, 11, 2))
    assert ledger.observed == [('1', 11, 2)] and not ledger.unknowns
    with pytest.raises(DispatchError, match='DISPATCH_BLOCKED'):
        send(gate, 'two')


def test_accounting_failure_does_not_retry_provider(dispatch):
    gate, ledger = dispatch
    def failed(*_): raise RuntimeError('private-accounting-details')
    ledger.observe = failed
    with pytest.raises(DispatchError, match='DISPATCH_RECONCILIATION_UNAVAILABLE'):
        send(gate, 'one')
    assert ledger.unknowns == ['1']
    with pytest.raises(DispatchError, match='DISPATCH_BLOCKED'):
        send(gate, 'two')


def test_foreign_permit_cannot_start_transport(dispatch):
    gate, ledger = dispatch
    ledger.authorize = lambda request: AttemptPermit('foreign', replace(request, intent=replace(gate.intent, owner_user_id='bob')))
    calls = []
    with pytest.raises(DispatchError, match='DISPATCH_PERMIT_INVALID'):
        send(gate, 'one', lambda _: calls.append(1))
    assert calls == []
