"""퀴즈 제출 요청 키 단위 저장(#110)을 실제 SQLite로 검증한다.

결과 4종(저장/기존 결과 재사용/요청 충돌/퀴즈 변경), 시도·요청 기록의 원자성, 동시 요청, 마이그레이션을 본다.
"""
import threading
from dataclasses import replace
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.domain.models import Evidence
from solo_leveling.domain.quiz import QuizQuestion
from solo_leveling.domain.quiz_attempt import (
    AnswerResult, AttemptValidationError, QuizAttempt, SubmitOutcome, REQUEST_KEY_MAX_LENGTH,
    submit_input_hash)
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection, init_db
from tests.unit.test_learning_persistence import setup_db, publish  # noqa: F401  (fixture)

STEP = 'preview'


def question():
    return QuizQuestion('multiple_choice', '무엇인가?', ('e1',), ('가', '나', '다'), 1, '나', '')


@pytest.fixture
def ctx(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.save_evidences(db, context.context_id, [Evidence('e1', 'c1', '첫째.', 'First.')])
    quiz = repo.save_quiz(db, context.context_id, STEP, [question()])
    return db, context.context_id, quiz.quiz_id


def make_attempt(context_id, quiz_id, attempt_id='a1', selected=1, evidence=('e1',)):
    result = AnswerResult(0, 'multiple_choice', '무엇인가?', ('가', '나', '다'), selected, None, '나', '',
                          selected == 1, evidence)
    return QuizAttempt(attempt_id, context_id, quiz_id, STEP, [result], '2026-11-01T00:00:00+00:00')


def submit(db, key, context_id, quiz_id, attempt_id='a1', answers=None, selected=1):
    answers = {0: selected} if answers is None else answers
    digest = submit_input_hash(context_id, STEP, quiz_id, answers)
    return repo.save_quiz_attempt_once(db, key, digest, make_attempt(context_id, quiz_id, attempt_id, selected))


def counts(db):
    with closing(get_connection(db)) as conn:
        return (conn.execute('SELECT COUNT(*) FROM quiz_attempts').fetchone()[0],
                conn.execute('SELECT COUNT(*) FROM quiz_attempt_answers').fetchone()[0],
                conn.execute('SELECT COUNT(*) FROM quiz_submit_requests').fetchone()[0])


# ── 결과 4종 ────────────────────────────────────────────────
def test_first_submit_saves_attempt_answers_and_request_together(ctx):
    db, context_id, quiz_id = ctx
    result = submit(db, 'k1', context_id, quiz_id)
    assert result.outcome is SubmitOutcome.SAVED
    assert result.attempt.attempt_id == 'a1'
    assert counts(db) == (1, 1, 1)
    assert repo.get_quiz_attempt(db, 'a1').correct_count == 1


def test_same_key_same_input_reuses_existing_result_without_saving(ctx):
    db, context_id, quiz_id = ctx
    submit(db, 'k1', context_id, quiz_id, attempt_id='a1')
    again = submit(db, 'k1', context_id, quiz_id, attempt_id='a2')  # 재시도는 새 attempt_id를 들고 온다
    assert again.outcome is SubmitOutcome.REUSED_EXISTING
    assert again.attempt.attempt_id == 'a1'
    assert repo.get_quiz_attempt(db, 'a2') is None
    assert counts(db) == (1, 1, 1)


def test_same_key_different_answer_is_conflict_and_saves_nothing(ctx):
    db, context_id, quiz_id = ctx
    submit(db, 'k1', context_id, quiz_id, selected=1)
    result = submit(db, 'k1', context_id, quiz_id, attempt_id='a2', selected=0)
    assert result.outcome is SubmitOutcome.REQUEST_CONFLICT and result.attempt is None
    assert counts(db) == (1, 1, 1)


@pytest.mark.parametrize('change', [
    {'context_id': 'other-context'}, {'step': 'review'}, {'quiz_id': 'other-quiz'},
])
def test_matching_digest_cannot_reuse_another_submission_scope(ctx, change):
    db, cid, qid = ctx
    submit(db, 'key', cid, qid)
    digest = submit_input_hash(cid, STEP, qid, {0: 1})
    other = replace(make_attempt(cid, qid, attempt_id='a2'), **change)
    prepared = repo.prepare_quiz_submission(db, other.context_id, other.step, other.quiz_id, 'key', digest)
    saved = repo.save_quiz_attempt_once(db, 'key', digest, other)
    for result in (prepared, saved):
        assert result.outcome is SubmitOutcome.REQUEST_CONFLICT
        assert result.attempt is None
    assert counts(db) == (1, 1, 1)
    assert submit(db, 'key', cid, qid).attempt.attempt_id == 'a1'


def test_completed_request_returns_original_even_after_quiz_regenerated(ctx):
    db, context_id, quiz_id = ctx
    submit(db, 'k1', context_id, quiz_id)
    new_quiz = repo.save_quiz(db, context_id, STEP, [question()])
    assert new_quiz.quiz_id != quiz_id  # 재생성하면 새 quiz_id가 붙는다(계약)
    again = submit(db, 'k1', context_id, quiz_id, attempt_id='a2')
    assert again.outcome is SubmitOutcome.REUSED_EXISTING and again.attempt.quiz_id == quiz_id
    assert counts(db) == (1, 1, 1)


def test_new_key_with_stale_quiz_id_is_quiz_changed_and_leaves_no_trace(ctx):
    db, context_id, quiz_id = ctx
    repo.save_quiz(db, context_id, STEP, [question()])
    result = submit(db, 'k1', context_id, quiz_id)
    assert result.outcome is SubmitOutcome.QUIZ_CHANGED and result.attempt is None
    assert counts(db) == (0, 0, 0)  # 예약 흔적도 없으므로 같은 키로 새 퀴즈에 다시 제출할 수 있다
    current = repo.get_quiz(db, context_id, STEP).quiz_id
    assert submit(db, 'k1', context_id, current).outcome is SubmitOutcome.SAVED


def test_different_key_saves_a_new_attempt(ctx):
    db, context_id, quiz_id = ctx
    submit(db, 'k1', context_id, quiz_id, attempt_id='a1')
    assert submit(db, 'k2', context_id, quiz_id, attempt_id='a2').outcome is SubmitOutcome.SAVED
    assert counts(db) == (2, 2, 2)


# ── 원자성·오류 ─────────────────────────────────────────────
def test_unknown_evidence_raises_and_rolls_back_everything(ctx):
    db, context_id, quiz_id = ctx
    with pytest.raises(AttemptValidationError):
        repo.save_quiz_attempt_once(
            db, 'k1', submit_input_hash(context_id, STEP, quiz_id, {0: 1}),
            make_attempt(context_id, quiz_id, evidence=('missing',)))
    assert counts(db) == (0, 0, 0)


def test_failure_after_attempt_insert_rolls_back_request_row(ctx):
    db, context_id, quiz_id = ctx
    repo.save_quiz_attempt(db, make_attempt(context_id, quiz_id, attempt_id='dup'))  # 같은 attempt_id가 이미 있다
    before = counts(db)
    with pytest.raises(Exception):
        submit(db, 'k1', context_id, quiz_id, attempt_id='dup')
    assert counts(db) == before
    assert submit(db, 'k1', context_id, quiz_id, attempt_id='ok').outcome is SubmitOutcome.SAVED


def test_prepare_new_request_with_unknown_context_raises_not_found(ctx):
    db, _, quiz_id = ctx
    context_id = 'missing-context'
    digest = submit_input_hash(context_id, STEP, quiz_id, {0: 1})
    with pytest.raises(ResourceNotFoundError):
        repo.prepare_quiz_submission(db, context_id, STEP, quiz_id, 'new-key', digest)
    assert counts(db) == (0, 0, 0)


def test_unknown_context_raises_not_found(ctx):
    db, context_id, quiz_id = ctx
    with pytest.raises(ResourceNotFoundError):
        submit(db, 'k1', 'nope', quiz_id)


@pytest.mark.parametrize('key', ['', '   ', 'x' * (REQUEST_KEY_MAX_LENGTH + 1), 'a\nb', None, 5])
def test_invalid_request_key_is_rejected_before_touching_db(ctx, key):
    db, context_id, quiz_id = ctx
    with pytest.raises(AttemptValidationError):
        repo.save_quiz_attempt_once(db, key, 'h', make_attempt(context_id, quiz_id))
    assert counts(db) == (0, 0, 0)


def test_quiz_missing_is_quiz_changed(ctx):
    db, context_id, quiz_id = ctx
    with closing(get_connection(db)) as conn, conn:
        conn.execute('DELETE FROM quiz_questions')
        conn.execute('DELETE FROM quizzes')
    assert submit(db, 'k1', context_id, quiz_id).outcome is SubmitOutcome.QUIZ_CHANGED


# ── 입력 해시 ───────────────────────────────────────────────
def test_hash_ignores_dict_order_but_keeps_answer_text_exactly():
    base = submit_input_hash('c', STEP, 'q', {0: 'Answer', 1: 2})
    assert base == submit_input_hash('c', STEP, 'q', {1: 2, 0: 'Answer'})
    for changed in ('answer', 'Answer ', 'Ａnswer'):  # 대소문자·공백·전각은 다른 입력
        assert base != submit_input_hash('c', STEP, 'q', {0: changed, 1: 2})
    assert submit_input_hash('c', STEP, 'q', {0: 1}) != submit_input_hash('c', STEP, 'q', {0: '1'})


@pytest.mark.parametrize('args', [('d', STEP, 'q'), ('c', 'after', 'q'), ('c', STEP, 'r')])
def test_hash_depends_on_context_step_and_quiz_id(args):
    assert submit_input_hash('c', STEP, 'q', {0: 1}) != submit_input_hash(*args, {0: 1})


def test_hash_does_not_contain_answer_text():
    assert 'secret-answer' not in submit_input_hash('c', STEP, 'q', {0: 'secret-answer'})


# ── 동시 요청 ───────────────────────────────────────────────
def test_concurrent_same_key_saves_exactly_once(ctx):
    db, context_id, quiz_id = ctx
    results, barrier = [], threading.Barrier(6)

    def worker(i):
        barrier.wait()
        results.append(submit(db, 'same', context_id, quiz_id, attempt_id=f'a{i}').outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(o.value for o in results) == ['reused_existing'] * 5 + ['saved']
    assert counts(db) == (1, 1, 1)


# ── 마이그레이션 ────────────────────────────────────────────
def test_init_db_adds_request_table_and_keeps_old_attempts_without_keys(ctx):
    db, context_id, quiz_id = ctx
    repo.save_quiz_attempt(db, make_attempt(context_id, quiz_id, attempt_id='old'))  # 이전 방식의 시도
    with closing(get_connection(db)) as conn, conn:
        conn.execute('DROP TABLE quiz_submit_requests')  # 이 기능 이전의 DB를 흉내
    init_db(db)
    init_db(db)  # 반복 실행해도 안전
    assert counts(db) == (1, 1, 0)  # 과거 시도는 그대로, 요청 키를 만들어 넣지 않는다
    assert submit(db, 'k1', context_id, quiz_id, attempt_id='new').outcome is SubmitOutcome.SAVED
    with closing(get_connection(db)) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_legacy_save_quiz_attempt_still_works_without_a_key(ctx):
    db, context_id, quiz_id = ctx
    repo.save_quiz_attempt(db, make_attempt(context_id, quiz_id))
    assert counts(db) == (1, 1, 0)
