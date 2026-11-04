"""ask 실행 기록(#119)을 실제 SQLite로 검증한다: 단일 실행권, 임대 만료 거부, 읽기 전용 조회, 보존·EXPIRED, 정리."""
import json
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from solo_leveling.domain.ask_execution import (
    HEARTBEAT_SECONDS, KEY_RETENTION_DAYS, LEASE_EXPIRED, LEASE_SECONDS, RESULT_RETENTION_DAYS, ExecutionOutcome as O,
    ExecutionState as S, ExecutionValidationError)
from solo_leveling.domain.models import RequestConflictError
from solo_leveling.infrastructure.database import ask_executions as ax
from solo_leveling.infrastructure.database.schema import get_connection
from tests.unit.test_learning_persistence import setup_db, publish  # noqa: F401  (fixture)

T0 = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
RESULT = json.dumps({'answer': '비밀-답변-본문'})


def at(**kw):
    return T0 + timedelta(**kw)


@pytest.fixture
def env(setup_db):
    db, chunks = setup_db
    return db, publish(db, chunks).context_id


def start(db, ctx, key='k1', digest='h1', now=T0):
    result = ax.begin_execution(db, key, digest, ctx, now)
    assert result.outcome is O.STARTED and result.owner_token
    return result.owner_token


def snapshot(db):
    with closing(get_connection(db)) as conn:
        return [tuple(r) for r in conn.execute('SELECT * FROM ask_executions ORDER BY request_key')]


# ── 계약 값 ────────────────────────────────────────────────
def test_agreed_initial_values():
    assert (HEARTBEAT_SECONDS, LEASE_SECONDS, RESULT_RETENTION_DAYS, KEY_RETENTION_DAYS) == (10, 60, 7, 30)


# ── 단일 실행권 ────────────────────────────────────────────
def test_first_begin_starts_and_gives_token_only_once(env):
    db, ctx = env
    first = ax.begin_execution(db, 'k1', 'h1', ctx, T0)
    assert first.outcome is O.STARTED and first.owner_token and first.view.state is S.RUNNING
    again = ax.begin_execution(db, 'k1', 'h1', ctx, at(seconds=5))
    assert again.outcome is O.IN_PROGRESS and again.owner_token is None
    assert len(snapshot(db)) == 1


def test_same_key_with_different_input_is_a_conflict_before_any_state(env):
    db, ctx = env
    start(db, ctx)
    with pytest.raises(RequestConflictError):
        ax.begin_execution(db, 'k1', 'other-hash', ctx, at(seconds=1))


def test_concurrent_begin_grants_the_execution_right_to_exactly_one(env):
    db, ctx = env
    results, barrier = [], threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(ax.begin_execution(db, 'race', 'h', ctx, T0))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    outcomes = sorted(r.outcome.value for r in results)
    assert outcomes == ['in_progress'] * 7 + ['started']
    assert len([r for r in results if r.owner_token]) == 1


def test_unknown_context_is_reported_and_nothing_is_stored(env):
    db, _ = env
    assert ax.begin_execution(db, 'k', 'h', 'missing', T0).outcome is O.CONTEXT_NOT_FOUND
    assert snapshot(db) == []


# ── 하트비트·임대 ──────────────────────────────────────────
def test_heartbeat_extends_the_lease_from_the_renewal_time(env):
    db, ctx = env
    token = start(db, ctx)
    renewed = ax.heartbeat_execution(db, 'k1', token, at(seconds=50))
    assert renewed.outcome is O.RENEWED
    assert ax.lookup_execution(db, 'k1', at(seconds=100)).state is S.RUNNING  # 원래 임대(60초)는 지났지만 갱신됨
    assert ax.lookup_execution(db, 'k1', at(seconds=110)).failure_code == LEASE_EXPIRED  # 갱신 시각 + 60초


def test_lease_boundary_is_exclusive(env):
    db, ctx = env
    token = start(db, ctx)
    assert ax.lookup_execution(db, 'k1', at(seconds=59, microseconds=999999)).state is S.RUNNING
    late = ax.heartbeat_execution(db, 'k1', token, at(seconds=60))
    assert late.outcome is O.LEASE_LOST


def test_late_heartbeat_is_rejected_even_from_the_original_owner(env):
    db, ctx = env
    token = start(db, ctx)
    before = snapshot(db)
    assert ax.heartbeat_execution(db, 'k1', token, at(seconds=61)).outcome is O.LEASE_LOST
    assert snapshot(db) == before  # 거부는 아무것도 바꾸지 않는다


def test_late_result_save_is_rejected_and_nothing_is_stored(env):
    db, ctx = env
    token = start(db, ctx)
    before = snapshot(db)
    assert ax.complete_execution(db, 'k1', token, RESULT, at(seconds=90)).outcome is O.LEASE_LOST
    assert snapshot(db) == before
    view = ax.lookup_execution(db, 'k1', at(seconds=90))
    assert (view.state, view.failure_code, view.result_json) == (S.FAILED, LEASE_EXPIRED, None)


def test_late_failure_record_is_rejected_too(env):
    db, ctx = env
    token = start(db, ctx)
    assert ax.fail_execution(db, 'k1', token, 'GENERATION_FAILED', at(seconds=90)).outcome is O.LEASE_LOST


def test_wrong_token_is_rejected_for_every_write(env):
    db, ctx = env
    start(db, ctx)
    before = snapshot(db)
    assert ax.heartbeat_execution(db, 'k1', 'wrong', at(seconds=1)).outcome is O.LEASE_LOST
    assert ax.complete_execution(db, 'k1', 'wrong', RESULT, at(seconds=1)).outcome is O.LEASE_LOST
    assert ax.fail_execution(db, 'k1', 'wrong', 'SOME_FAILURE', at(seconds=1)).outcome is O.LEASE_LOST
    assert snapshot(db) == before


def test_unknown_key_writes_report_not_found(env):
    db, ctx = env
    assert ax.heartbeat_execution(db, 'nope', 'tok', T0).outcome is O.NOT_FOUND
    assert ax.complete_execution(db, 'nope', 'tok', RESULT, T0).outcome is O.NOT_FOUND


def test_expired_lease_is_never_handed_to_a_new_owner_and_never_restarts(env):
    db, ctx = env
    start(db, ctx)
    again = ax.begin_execution(db, 'k1', 'h1', ctx, at(minutes=10))  # 같은 키, 임대 만료 뒤
    assert again.outcome is O.FAILED and again.owner_token is None
    assert again.view.failure_code == LEASE_EXPIRED
    assert len(snapshot(db)) == 1  # 새 행도, 새 토큰도 없다
    fresh = ax.begin_execution(db, 'k2', 'h1', ctx, at(minutes=10))  # 새 실행은 새 키로만
    assert fresh.outcome is O.STARTED


# ── 결과·실패 ──────────────────────────────────────────────
def test_complete_stores_result_and_lookup_returns_it_until_retention_ends(env):
    db, ctx = env
    token = start(db, ctx)
    saved = ax.complete_execution(db, 'k1', token, RESULT, at(seconds=20))
    assert saved.outcome is O.SAVED
    view = ax.lookup_execution(db, 'k1', at(days=6))
    assert (view.state, view.result_json) == (S.COMPLETED, RESULT)
    gone = ax.lookup_execution(db, 'k1', at(seconds=20, days=7))
    assert (gone.state, gone.result_json) == (S.EXPIRED, None)  # 결과 7일
    assert ax.begin_execution(db, 'k1', 'h1', ctx, at(days=8)).outcome is O.EXPIRED  # 자동 재실행 아님
    assert len(snapshot(db)) == 1


def test_second_completion_and_completion_after_failure_are_rejected(env):
    db, ctx = env
    token = start(db, ctx)
    assert ax.complete_execution(db, 'k1', token, RESULT, at(seconds=1)).outcome is O.SAVED
    assert ax.complete_execution(db, 'k1', token, '{"x":1}', at(seconds=2)).outcome is O.LEASE_LOST
    token2 = start(db, ctx, key='k2')
    assert ax.fail_execution(db, 'k2', token2, 'GENERATION_FAILED', at(seconds=1)).outcome is O.SAVED
    assert ax.complete_execution(db, 'k2', token2, RESULT, at(seconds=2)).outcome is O.LEASE_LOST
    assert ax.lookup_execution(db, 'k2', at(seconds=3)).failure_code == 'GENERATION_FAILED'


def test_repeated_begin_on_a_finished_key_reports_the_stored_state(env):
    db, ctx = env
    token = start(db, ctx)
    ax.complete_execution(db, 'k1', token, RESULT, at(seconds=1))
    again = ax.begin_execution(db, 'k1', 'h1', ctx, at(seconds=2))
    assert again.outcome is O.COMPLETED and again.owner_token is None and again.view.result_json == RESULT


def test_key_record_expiry_turns_everything_into_expired_until_purged(env):
    db, ctx = env
    token = start(db, ctx)
    ax.fail_execution(db, 'k1', token, 'GENERATION_FAILED', at(seconds=1))
    assert ax.lookup_execution(db, 'k1', at(days=29)).state is S.FAILED
    assert ax.lookup_execution(db, 'k1', at(days=30)).state is S.EXPIRED


# ── 읽기 전용 조회 ─────────────────────────────────────────
def test_lookup_never_changes_stored_data(env):
    db, ctx = env
    start(db, ctx)
    start(db, ctx, key='k2')
    before = snapshot(db)
    for offset in (0, 30, 61, 3600, 86400 * 8, 86400 * 40):
        ax.lookup_execution(db, 'k1', at(seconds=offset))
        ax.lookup_execution(db, 'k2', at(seconds=offset))
        ax.lookup_execution(db, 'unknown', at(seconds=offset))
    assert snapshot(db) == before


def test_lookup_of_unknown_key_is_not_found_and_does_not_start_anything(env):
    db, ctx = env
    assert ax.lookup_execution(db, 'unknown', T0).state is S.NOT_FOUND
    assert snapshot(db) == []


def test_lookup_view_never_contains_the_owner_token(env):
    db, ctx = env
    token = start(db, ctx)
    view = ax.lookup_execution(db, 'k1', T0)
    assert token not in repr(view)
    assert token not in repr(ax.begin_execution(db, 'k1', 'h1', ctx, at(seconds=1)))


def test_state_survives_reconnect_like_a_server_restart(env):
    db, ctx = env
    token = start(db, ctx)
    ax.heartbeat_execution(db, 'k1', token, at(seconds=30))
    # 새 연결만으로 같은 상태를 읽고 같은 토큰으로 이어서 갱신·완료할 수 있다.
    assert ax.lookup_execution(db, 'k1', at(seconds=40)).state is S.RUNNING
    assert ax.complete_execution(db, 'k1', token, RESULT, at(seconds=45)).outcome is O.SAVED


# ── 정리 ──────────────────────────────────────────────────
def test_purge_plan_changes_nothing_and_matches_what_purge_does(env):
    db, ctx = env
    for key in ('a', 'b'):
        ax.complete_execution(db, key, start(db, ctx, key=key), RESULT, at(seconds=1))
    start(db, ctx, key='late', now=at(days=29, hours=23))  # 키 보존이 아직 지나지 않은 실행
    when = at(days=31)
    before = snapshot(db)
    plan = ax.plan_execution_purge(db, when)
    assert snapshot(db) == before
    assert (plan.results_to_clear, plan.rows_to_delete) == (0, 2)  # a·b만 키 보존(30일)이 지났다
    assert ax.purge_expired_executions(db, when).rows_deleted == 2
    assert [row[0] for row in snapshot(db)] == ['late']


def test_purge_clears_expired_results_first_then_deletes_expired_keys(env):
    db, ctx = env
    ax.complete_execution(db, 'a', start(db, ctx, key='a'), RESULT, at(seconds=1))
    when = at(days=8)
    plan = ax.plan_execution_purge(db, when)
    assert (plan.results_to_clear, plan.rows_to_delete) == (1, 0)
    result = ax.purge_expired_executions(db, when)
    assert (result.results_cleared, result.rows_deleted, result.complete) == (1, 0, True)
    with closing(get_connection(db)) as conn:
        assert conn.execute("SELECT result_json FROM ask_executions WHERE request_key='a'").fetchone()[0] is None
    assert ax.lookup_execution(db, 'a', when).state is S.EXPIRED  # 키 기록은 남아 있다
    later = ax.purge_expired_executions(db, at(days=31))
    assert later.rows_deleted == 1
    assert ax.lookup_execution(db, 'a', at(days=31)).state is S.NOT_FOUND


def test_purge_never_deletes_an_execution_with_a_valid_lease(env):
    db, ctx = env
    start(db, ctx)
    # 30일 동안 하트비트가 이어진 경우를 직접 만든다(키 보존은 지났지만 임대는 유효).
    with closing(get_connection(db)) as conn, conn:
        conn.execute('UPDATE ask_executions SET lease_expires_at=? WHERE request_key=?',
                     (ax._ts(at(days=30, seconds=55)), 'k1'))
    assert ax.plan_execution_purge(db, at(days=30, seconds=1)).rows_to_delete == 0
    assert ax.purge_expired_executions(db, at(days=30, seconds=1)).rows_deleted == 0
    assert len(snapshot(db)) == 1
    assert ax.purge_expired_executions(db, at(days=30, seconds=56)).rows_deleted == 1  # 임대가 끝나면 정리된다


def test_purge_in_batches_can_stop_and_resume(env):
    db, ctx = env
    for i in range(5):
        ax.complete_execution(db, f'k{i}', start(db, ctx, key=f'k{i}'), RESULT, at(seconds=1))
    when = at(days=40)
    first = ax.purge_expired_executions(db, when, batch_size=2, max_batches=1)
    assert (first.rows_deleted, first.complete) == (2, False)
    rest = ax.purge_expired_executions(db, when, batch_size=2)
    assert (rest.rows_deleted, rest.complete) == (3, True)
    assert snapshot(db) == []


def test_deleting_the_context_removes_its_execution_records(env):
    db, ctx = env
    start(db, ctx)
    with closing(get_connection(db)) as conn, conn:
        conn.execute('DELETE FROM learning_contexts WHERE context_id=?', (ctx,))
    assert snapshot(db) == []


# ── 입력 검증 ──────────────────────────────────────────────
@pytest.mark.parametrize('call', [
    lambda db, ctx: ax.begin_execution(db, '', 'h', ctx, T0),
    lambda db, ctx: ax.begin_execution(db, 'k', ' ', ctx, T0),
    lambda db, ctx: ax.begin_execution(db, 'k' * 201, 'h', ctx, T0),
    lambda db, ctx: ax.begin_execution(db, 'k', 'h', ctx, datetime(2026, 1, 1)),  # 시간대 없는 시각
    lambda db, ctx: ax.complete_execution(db, 'k', 'tok', 'not json', T0),
    lambda db, ctx: ax.complete_execution(db, 'k', 'tok', 'x' * 262145, T0),
    lambda db, ctx: ax.fail_execution(db, 'k', 'tok', 'lowercase code', T0),
    lambda db, ctx: ax.fail_execution(db, 'k', 'tok', '비밀 본문', T0),
    lambda db, ctx: ax.purge_expired_executions(db, T0, batch_size=0),
    lambda db, ctx: ax.purge_expired_executions(db, T0, batch_size=True),
])
def test_invalid_inputs_are_rejected_without_echoing_values(env, call):
    db, ctx = env
    with pytest.raises(ExecutionValidationError) as info:
        call(db, ctx)
    assert '비밀' not in str(info.value)
    assert snapshot(db) == []
