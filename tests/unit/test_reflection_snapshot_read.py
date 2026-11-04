"""#161: 읽기 전·후 답변 조회가 맥락과 답변을 같은 시점에 읽는지 실제 SQLite로 검증한다."""
import sqlite3
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.application.reflection.service import SESSION_NOT_STARTED, ReflectionService
from solo_leveling.domain.reflection import PHASE_AFTER, PHASE_BEFORE
from solo_leveling.infrastructure.database import repository as repo
from tests.unit.test_learning_persistence import publish, setup_db  # noqa: F401


@pytest.fixture
def ctx(setup_db):  # noqa: F811
    db, chunks = setup_db
    return db, publish(db, chunks).context_id


class InterleavingConnection:
    """읽기 문장 사이(두 번째 execute 직전)와 읽기가 끝난 뒤(close)에 hook을 한 번 실행하는 연결 감싸개.

    읽기를 여러 문장으로 나누면 그 사이에 다른 연결이 끼어들고, 한 문장이면 끼어들 틈이 없다.
    """

    def __init__(self, conn, hook):
        self._conn, self._hook, self._executed = conn, hook, 0

    def execute(self, *args, **kwargs):
        if self._executed:
            self._hook()
        self._executed += 1
        return self._conn.execute(*args, **kwargs)

    def close(self):
        self._conn.close()
        self._hook()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def delete_context_with_answers(db, timeout=0.05):
    """답변과 맥락을 한 트랜잭션으로 지운다. 잠겨 있어 못 지우면 False(읽기가 끝나기 전에는 끼어들 수 없다는 뜻)."""
    try:
        with closing(sqlite3.connect(db, timeout=timeout)) as conn, conn:
            conn.execute('PRAGMA foreign_keys = OFF')
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM reflection_answers WHERE context_id IN (SELECT context_id FROM learning_contexts)')
            conn.execute('DELETE FROM learning_progress')
            conn.execute('DELETE FROM learning_contexts')
        return True
    except sqlite3.OperationalError:
        return False


def intercept_with(monkeypatch, db, hook):
    real = repo.get_connection
    monkeypatch.setattr(repo, 'get_connection', lambda path: InterleavingConnection(real(path), hook))


def test_missing_context_is_not_found_not_unwritten(ctx):
    db, _ = ctx
    with pytest.raises(ResourceNotFoundError):
        repo.get_reflection_answers_all(db, 'no-such-context')
    with pytest.raises(ResourceNotFoundError):
        ReflectionService(db).get_comparison('no-such-context')


def test_existing_context_without_answers_is_unwritten(ctx):
    db, context_id = ctx
    assert repo.get_reflection_answers_all(db, context_id) == []
    comparison = ReflectionService(db).get_comparison(context_id)
    assert (comparison.before.status, comparison.before.written) == (SESSION_NOT_STARTED, 0)
    assert (comparison.after.status, comparison.after.written) == (SESSION_NOT_STARTED, 0)


def test_empty_draft_is_distinct_from_unwritten(ctx):
    db, context_id = ctx
    service = ReflectionService(db)
    service.save_answer(context_id, PHASE_BEFORE, 'before.hypothesis', '', 'draft')
    answers = repo.get_reflection_answers_all(db, context_id)
    assert [(a.template_id, a.text, a.status) for a in answers] == [('before.hypothesis', '', 'draft')]
    assert service.get_comparison(context_id).before.written == 1


def test_phase_filter_returns_only_that_phase_and_keeps_empty_phase_as_existing_context(ctx):
    db, context_id = ctx
    ReflectionService(db).save_answer(context_id, PHASE_BEFORE, 'before.hypothesis', '가설', 'draft')
    assert [a.template_id for a in repo.get_reflection_answers(db, context_id, PHASE_BEFORE)] == ['before.hypothesis']
    assert repo.get_reflection_answers(db, context_id, PHASE_AFTER) == []  # 맥락은 있으나 이 시점 답변은 없음
    with pytest.raises(ResourceNotFoundError):
        repo.get_reflection_answers(db, 'no-such-context', PHASE_AFTER)


class Deleter:
    """읽기 중에 끼어드는 삭제. 훅이 불릴 때마다 아직 안 지워졌으면 다시 시도하고, 성공은 한 번만 센다."""

    def __init__(self, db, delete=None):
        self.db, self.delete, self.done, self.attempts = db, delete or delete_context_with_answers, False, 0

    def attempt(self, timeout=0.05):
        if not self.done:
            self.attempts += 1
            self.done = self.delete(self.db, timeout)


def remaining(db, context_id):
    """별도 연결로 실제 커밋된 상태를 본다: (맥락 수, 그 맥락의 답변 수)."""
    with closing(sqlite3.connect(db)) as conn:
        return (conn.execute('SELECT COUNT(*) FROM learning_contexts WHERE context_id=?', (context_id,)).fetchone()[0],
                conn.execute('SELECT COUNT(*) FROM reflection_answers WHERE context_id=?', (context_id,)).fetchone()[0])


def run_concurrent_delete(ctx, monkeypatch, read, delete=None):
    db, context_id = ctx
    service = ReflectionService(db)
    service.save_answer(context_id, PHASE_BEFORE, 'before.hypothesis', '삭제 전 초안', 'draft')
    assert remaining(db, context_id) == (1, 1)
    deleter = Deleter(db, delete)
    intercept_with(monkeypatch, db, deleter.attempt)
    try:
        result = (repo.get_reflection_answers_all(db, context_id) if read == 'all'
                  else service.get_comparison(context_id))
    except ResourceNotFoundError:
        result = 'not-found'
    # 읽기 연결이 닫힌 뒤, 읽기 중에 잠겨서 못 지웠다면 다시 시도한다(잠금이 풀려 있어야 한다).
    deleter.attempt(timeout=5)
    # 어느 반환 분기를 택했든 삭제가 실제로 커밋됐는지 별도 연결로 확인한다.
    assert deleter.done, '삭제가 실행되지 않았다'
    assert remaining(db, context_id) == (0, 0), '맥락과 답변이 함께 사라져야 한다'

    if result == 'not-found':
        return result
    texts = ([a.text for a in result] if read == 'all'
             else [i.answer.text for i in result.before.items if i.answer is not None])
    assert texts == ['삭제 전 초안']   # 삭제 전의 일관된 결과만 허용(미작성 [] 이면 안 된다)
    return result


@pytest.mark.parametrize('read', ['all', 'comparison'])
def test_concurrent_delete_never_looks_like_unwritten(ctx, monkeypatch, read):
    """읽는 도중 다른 연결이 답변과 맥락을 함께 지워도, 삭제 전 초안이거나 not-found여야지 미작성이면 안 된다."""
    run_concurrent_delete(ctx, monkeypatch, read)


@pytest.mark.parametrize('read', ['all', 'comparison'])
def test_control_without_the_delete_the_scenario_fails(ctx, monkeypatch, read):
    """대조 실험: 삭제를 생략(항상 False)하면 같은 검사가 실패해야 한다. 그렇지 않으면 위 테스트는 삭제 없이도 통과한다."""
    with pytest.raises(AssertionError):
        run_concurrent_delete(ctx, monkeypatch, read, delete=lambda db, timeout: False)


@pytest.mark.parametrize('read', ['all', 'comparison'])
def test_delayed_first_attempt_is_retried_and_committed(ctx, monkeypatch, read):
    """첫 시도가 잠겨 실패하고 다음 시도가 성공하는 경우도 정확히 표현한다(첫 결과만 기록하지 않는다)."""
    calls = []

    def flaky(db, timeout):
        calls.append(timeout)
        return False if len(calls) == 1 else delete_context_with_answers(db, timeout)

    run_concurrent_delete(ctx, monkeypatch, read, delete=flaky)
    assert len(calls) >= 2                                  # 첫 시도 실패 뒤 재시도로 지워졌다
