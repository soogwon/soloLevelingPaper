"""서버·외부 API 없이 C의 조회/표시 경계를 검증한다. 실제 원자 접수 검증은 아니다."""

from dataclasses import asdict, replace
import socket

import pytest

from solo_leveling.interfaces.mcp.remote_consent_contract import (
    BudgetState as B, ConsentState as C, ExecutionState as E,
    Intent, Snapshot, recover_guidance,
)

INTENT = Intent('owner-a', 'version-a', 'question', 'hash-a', 'key-a')
BASE = Snapshot(INTENT, C.CURRENT, C.CURRENT, B.AVAILABLE, E.ABSENT, access_allowed=True)


class FakeReader:
    def __init__(self, snapshot=BASE):
        self.snapshot = snapshot
        self.reads = []
        self.submissions = 0

    def read(self, intent):
        self.reads.append(intent)
        return self.snapshot

    def submit(self, *args):
        self.submissions += 1
        pytest.fail('상태 복구에서 제출하면 안 됩니다')


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('실제 네트워크 호출 금지')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


@pytest.mark.parametrize('purpose', ['storage', 'ai'])
@pytest.mark.parametrize('state', [C.MISSING, C.OUTDATED, C.REVOKED])
def test_separate_consent_and_safe_web_action(purpose, state):
    reader = FakeReader(replace(BASE, **{purpose + '_consent': state}))
    view = recover_guidance(reader, INTENT)
    assert view.state == f'{purpose}_{state.value}'
    assert view.next_action == 'review_consent'
    assert not view.automatic_paid_retry
    assert reader.submissions == 0


@pytest.mark.parametrize('field', ['owner_id', 'target_id', 'operation', 'input_hash', 'request_key'])
def test_scope_mismatch_is_indistinguishable_from_missing(field):
    other = replace(INTENT, **{field: 'other-secret'})
    assert recover_guidance(FakeReader(replace(BASE, intent=other)), INTENT) == recover_guidance(FakeReader(None), INTENT)


def test_response_loss_and_new_presenter_recover_same_completed_execution():
    reader = FakeReader(replace(BASE, execution=E.COMPLETED, execution_id='job-a', saved_result_available=True))
    first = recover_guidance(reader, INTENT)
    second = recover_guidance(reader, INTENT)
    assert first == second and second.next_action == 'read_result'
    assert reader.snapshot.execution_id == 'job-a'
    assert reader.reads == [INTENT, INTENT] and reader.submissions == 0


@pytest.mark.parametrize('consent', list(C))
@pytest.mark.parametrize('budget', list(B))
def test_saved_result_survives_consent_and_budget_changes(consent, budget):
    reader = FakeReader(replace(BASE, ai_consent=consent, budget=budget, execution=E.COMPLETED,
                                execution_id='job-a', saved_result_available=True))
    view = recover_guidance(reader, INTENT)
    assert view.can_read_result and view.next_action == 'read_result'
    assert not view.automatic_paid_retry


def test_access_revocation_hides_even_saved_result():
    snapshot = replace(BASE, execution=E.COMPLETED, execution_id='job-a', saved_result_available=True, access_allowed=False)
    assert recover_guidance(FakeReader(snapshot), INTENT) == recover_guidance(FakeReader(None), INTENT)


def test_stale_card_refresh_does_not_consume_or_replace_consent():
    reader = FakeReader(BASE)
    assert recover_guidance(reader, INTENT).state == 'review_submission'
    reader.snapshot = replace(BASE, ai_consent=C.REVOKED)
    assert recover_guidance(reader, INTENT).state == 'ai_revoked'
    assert reader.snapshot.ai_consent is C.REVOKED and reader.submissions == 0


@pytest.mark.parametrize('execution', [E.RUNNING, E.FAILED, E.UNKNOWN])
def test_existing_execution_is_never_resubmitted(execution):
    reader = FakeReader(replace(BASE, execution=execution, execution_id='job-a', budget=B.USAGE_UNKNOWN))
    view = recover_guidance(reader, INTENT)
    assert view.next_action == 'refresh_status' and not view.automatic_paid_retry
    assert '사용량 확인' in view.message and reader.submissions == 0


@pytest.mark.parametrize('budget,state', [(B.LIMITED, 'budget_limited'), (B.USAGE_UNKNOWN, 'usage_unknown')])
def test_limit_is_not_insufficient_evidence(budget, state):
    view = recover_guidance(FakeReader(replace(BASE, budget=budget)), INTENT)
    assert view.state == state and not view.automatic_paid_retry
    assert 'insufficient_evidence' not in str(asdict(view))


def test_reader_failure_redacts_secrets(capsys):
    class BrokenReader:
        def read(self, intent):
            raise RuntimeError('secret-token original-question private-path')
    view = recover_guidance(BrokenReader(), INTENT)
    assert view.state == 'unavailable'
    assert 'secret-token' not in str(asdict(view))
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('changes', [dict(execution='completed'), dict(ai_consent='current'),
    dict(budget='available'), dict(saved_result_available=1), dict(execution=E.RUNNING),
    dict(execution_id='orphan'), dict(saved_result_available=True)])
def test_malformed_snapshot_fails_closed(changes):
    assert recover_guidance(FakeReader(replace(BASE, **changes)), INTENT).state == 'unavailable'


def test_guidance_does_not_expose_scope_or_authorize_submission():
    view = recover_guidance(FakeReader(), INTENT)
    assert view.next_action == 'review_submission' and not view.automatic_paid_retry
    for secret in asdict(INTENT).values():
        assert secret not in str(asdict(view))


def test_missing_access_decision_does_not_allow_read_or_submission():
    snapshot = Snapshot(INTENT, C.CURRENT, C.CURRENT, B.AVAILABLE, E.COMPLETED,
                        execution_id='job-a', saved_result_available=True)
    assert recover_guidance(FakeReader(snapshot), INTENT) == recover_guidance(FakeReader(None), INTENT)


def test_completed_without_saved_result_does_not_offer_result():
    reader = FakeReader(replace(BASE, execution=E.COMPLETED, execution_id='job-a'))
    view = recover_guidance(reader, INTENT)
    assert view.state == 'completed' and not view.can_read_result
    assert view.next_action == 'refresh_status'
    assert '조회 가능한 결과를 확인하지 못했습니다' in view.message
    assert reader.submissions == 0
