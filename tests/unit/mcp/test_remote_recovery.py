"""키 저장·서버 조회 대역으로 계정 전환과 자동 재제출 금지를 검증한다."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from solo_leveling.application.remote_replay import ExecutionLookup, StoredExecution, ReplayError
from solo_leveling.interfaces.mcp.remote_recovery import AccountRecovery

A = ExecutionLookup('alice', 'ask_paper', 'key', 'hash', 'context')


class Keys:
    def __init__(self): self.rows = {}
    def get(self, *key): return self.rows.get(key)
    def put(self, lookup):
        key = (lookup.owner_user_id, lookup.operation, lookup.request_key)
        if key in self.rows and self.rows[key] != lookup:
            raise ValueError('합성 키 충돌')
        self.rows[key] = lookup


def setup():
    keys = Keys()
    model = AccountRecovery(keys)
    model.select_account('alice')
    model.remember(A)
    return model, keys


def row(lookup=A):
    return StoredExecution(lookup, 'same-execution', 'queued', 'usage_unknown', access_allowed=True)


def test_new_presenter_restores_same_key_without_resubmission():
    model, keys = setup()
    calls = []
    def read(*key): calls.append(key); return row()
    for presenter in (model, AccountRecovery(keys)):
        presenter.select_account('alice')
        result = presenter.recover('ask_paper', 'key', NS(lookup=read))
        assert result.status == 'restored'
        assert result.result.execution_id == 'same-execution'
        assert result.result.state == 'queued' and result.result.budget_state == 'usage_unknown'
    assert calls == [('alice', 'ask_paper', 'key')] * 2
    assert len(keys.rows) == 1


def test_account_change_clears_result_and_never_uses_other_accounts_key():
    model, keys = setup()
    model.recover('ask_paper', 'key', NS(lookup=lambda *_: row()))
    model.select_account('bob')
    assert model.snapshot().result is None
    def forbidden(*_): pytest.fail('다른 계정의 복구 키를 조회하면 안 됩니다')
    assert model.recover('ask_paper', 'key', NS(lookup=forbidden)).status == 'not_found'
    model.select_account(None)
    assert model.recover('ask_paper', 'key', NS(lookup=forbidden)).status == 'signed_out'
    model.select_account('alice')
    assert model.recover('ask_paper', 'key', NS(lookup=lambda *_: row())).status == 'restored'


@pytest.mark.parametrize('back_to_alice', [False, True])
def test_late_response_after_account_change_is_discarded(back_to_alice):
    model, _ = setup()
    def read(*_):
        model.select_account('bob')
        if back_to_alice: model.select_account('alice')
        return row()
    assert model.recover('ask_paper', 'key', NS(lookup=read)).status == 'idle'
    assert model.snapshot().result is None


@pytest.mark.parametrize('outcome,expected', [('missing','not_found'), ('denied','not_found'),
    ('conflict','conflict'), ('broken','unavailable'), ('corrupt','unavailable')])
def test_failures_clear_previous_result_and_do_not_create_keys(outcome, expected):
    model, keys = setup()
    model.recover('ask_paper', 'key', NS(lookup=lambda *_: row()))
    def read(*_):
        if outcome == 'broken': raise RuntimeError('private-token-and-question')
        if outcome == 'missing': return None
        if outcome == 'denied': return replace(row(), access_allowed=False)
        if outcome == 'conflict': return row(replace(A, input_hash='changed'))
        return replace(row(), state='invalid')
    result = model.recover('ask_paper', 'key', NS(lookup=read))
    assert result.status == expected and result.result is None
    assert list(keys.rows.values()) == [A]


def test_foreign_key_from_broken_store_never_reaches_server():
    model, keys = setup()
    keys.rows[('alice','ask_paper','key')] = replace(A, owner_user_id='bob')
    def forbidden(*_): pytest.fail('소유자가 다른 저장 키는 전송하면 안 됩니다')
    assert model.recover('ask_paper', 'key', NS(lookup=forbidden)).status == 'not_found'


def test_remember_rejects_cross_account_and_conflicting_input():
    model, keys = setup()
    with pytest.raises(ReplayError): model.remember(replace(A, owner_user_id='bob'))
    with pytest.raises(ReplayError): model.remember(replace(A, input_hash='other'))
    assert list(keys.rows.values()) == [A]


def test_later_selection_in_same_account_cannot_be_overwritten():
    model, _ = setup()
    other = replace(A, request_key='other')
    model.remember(other)
    def delayed(*_):
        model.recover('ask_paper', 'other', NS(lookup=lambda *_: replace(row(other), execution_id='new-selection')))
        return row()
    result = model.recover('ask_paper', 'key', NS(lookup=delayed))
    assert result.result.execution_id == 'new-selection'
