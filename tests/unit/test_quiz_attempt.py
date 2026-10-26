"""퀴즈 시도·답안 기록(M1-10, #55)의 모델·저장·조회를 실제 SQLite로 검증한다."""
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.domain.models import Evidence
from solo_leveling.domain.quiz import QuizQuestion
from solo_leveling.domain.quiz_attempt import AnswerResult, AttemptValidationError, QuizAttempt
from solo_leveling.infrastructure.database import repository as repo
from solo_leveling.infrastructure.database.schema import get_connection, init_db
from tests.unit.test_learning_persistence import setup_db, publish


@pytest.fixture
def ctx(setup_db):
    db, chunks = setup_db
    context = publish(db, chunks)
    repo.save_evidences(db, context.context_id, [
        Evidence('e1', 'c1', '첫째.', 'First.'),
        Evidence('e2', 'c2', '둘째.', 'Second.'),
    ])
    return db, context.context_id


def mc_result(position=0, correct=True, evidence=('e1',)):
    return AnswerResult(position, 'multiple_choice', f'객관식 {position}', ('가', '나', '다'),
                        1 if correct else 0, None, '나', '', correct, evidence)


def short_result(position=1, correct=False, evidence=('e2',), text='오답'):
    return AnswerResult(position, 'short_answer', f'단답 {position}', (), None, text, 'Second',
                        'Second라고 쓰면 정답', correct, evidence)


def attempt(context_id, attempt_id='a1', step='preview', results=None, submitted_at='2026-10-26T00:00:00+00:00',
            quiz_id='q1'):
    return QuizAttempt(attempt_id, context_id, quiz_id, step,
                       results if results is not None else [mc_result(), short_result()], submitted_at)


# ── 모델 ────────────────────────────────────────────────────
def test_score_counts_correct_answers_and_unanswered_as_wrong():
    skipped = AnswerResult(2, 'short_answer', '무응답', (), None, None, '정답', '기준', False, ('e1',))
    a = attempt('c', results=[mc_result(), short_result(), skipped])
    assert (a.correct_count, a.total_count) == (1, 3)
    assert a.score == pytest.approx(1 / 3)
    assert [r.position for r in a.wrong_results] == [1, 2]
    assert skipped.answered is False and mc_result().answered and short_result().answered


@pytest.mark.parametrize('kw', [
    {'step': 'bogus'},
    {'results': []},
])
def test_invalid_attempt(kw):
    with pytest.raises(AttemptValidationError):
        attempt('c', **kw)


def test_invalid_result():
    with pytest.raises(AttemptValidationError):
        mc_result(evidence=())
    with pytest.raises(AttemptValidationError):
        AnswerResult(0, 'essay', 'x', (), None, None, 'a', '', True, ('e1',))
    with pytest.raises(AttemptValidationError):
        AnswerResult(0, 'short_answer', 'x', (), None, 'a', 'a', '', 1, ('e1',))


# ── 저장·조회 ───────────────────────────────────────────────
def test_save_and_get_round_trip(ctx):
    db, cid = ctx
    repo.save_quiz_attempt(db, attempt(cid))
    got = repo.get_quiz_attempt(db, 'a1')
    assert (got.context_id, got.quiz_id, got.step, got.correct_count, got.total_count) == (cid, 'q1', 'preview', 1, 2)
    first, second = got.results
    assert (first.selected_index, first.choices, first.correct_answer, first.is_correct) == (1, ('가', '나', '다'), '나', True)
    assert (second.submitted_text, second.correct_answer, second.grading_criteria, second.is_correct) == (
        '오답', 'Second', 'Second라고 쓰면 정답', False)
    assert repo.get_quiz_attempt(db, 'missing') is None


def test_list_attempts_is_chronological_and_filterable_by_step(ctx):
    db, cid = ctx
    repo.save_quiz_attempt(db, attempt(cid, 'a1', 'preview', submitted_at='2026-10-26T01:00:00+00:00'))
    repo.save_quiz_attempt(db, attempt(cid, 'a2', 'review', submitted_at='2026-10-26T02:00:00+00:00'))
    repo.save_quiz_attempt(db, attempt(cid, 'a3', 'preview', submitted_at='2026-10-26T03:00:00+00:00'))
    assert [a.attempt_id for a in repo.list_quiz_attempts(db, cid)] == ['a1', 'a2', 'a3']
    assert [a.attempt_id for a in repo.list_quiz_attempts(db, cid, 'preview')] == ['a1', 'a3']
    assert repo.list_quiz_attempts(db, cid, 'structure') == []


def test_wrong_answer_evidence_points_to_the_original_text(ctx):
    db, cid = ctx
    saved = repo.save_quiz_attempt(db, attempt(cid))
    detail = repo.get_attempt_wrong_evidence(db, saved).evidence
    assert [(d.quote_original, d.pdf_page) for d in detail] == [('Second.', 2)]  # 틀린 문항(e2)만


def test_all_correct_attempt_has_no_wrong_evidence(ctx):
    db, cid = ctx
    saved = repo.save_quiz_attempt(db, attempt(cid, results=[mc_result(correct=True)]))
    assert repo.get_attempt_wrong_evidence(db, saved).evidence == ()


def test_attempts_survive_quiz_regeneration(ctx):
    """퀴즈를 다시 만들어 quizzes 행이 교체돼도 과거 시도(점수·오답 근거)가 남아 있다."""
    db, cid = ctx
    quiz = repo.save_quiz(db, cid, 'preview', [QuizQuestion('short_answer', '질문', ('e1',),
                                                           answer='답', grading_criteria='기준')])
    repo.save_quiz_attempt(db, attempt(cid, quiz_id=quiz.quiz_id))
    repo.save_quiz(db, cid, 'preview', [QuizQuestion('short_answer', '새 질문', ('e2',),
                                                    answer='새 답', grading_criteria='새 기준')])
    old = repo.get_quiz_attempt(db, 'a1')
    assert old.quiz_id == quiz.quiz_id and old.correct_count == 1
    assert [r.prompt for r in old.results] == ['객관식 0', '단답 1']


def test_unknown_evidence_and_context_are_rejected_without_partial_save(ctx):
    db, cid = ctx
    with pytest.raises(AttemptValidationError):
        repo.save_quiz_attempt(db, attempt(cid, results=[mc_result(), short_result(evidence=('nope',))]))
    with pytest.raises(ResourceNotFoundError):
        repo.save_quiz_attempt(db, attempt('missing'))
    assert repo.list_quiz_attempts(db, cid) == []
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM quiz_attempt_answers').fetchone()[0] == 0


def test_evidence_of_another_context_is_rejected(ctx):
    db, cid = ctx
    from solo_leveling.domain.models import LearningContext
    repo.create_learning_context(
        db, LearningContext('ctx2', 'v1', 'p1', 't1', known_concepts=['다른 개념'], embedding_set_id='idx'))
    with pytest.raises(AttemptValidationError):
        repo.save_quiz_attempt(db, attempt('ctx2'))


def test_attempt_ids_are_unique(ctx):
    db, cid = ctx
    repo.save_quiz_attempt(db, attempt(cid))
    with pytest.raises(Exception):
        repo.save_quiz_attempt(db, attempt(cid))
    assert len(repo.list_quiz_attempts(db, cid)) == 1


def test_schema_is_repeatable_and_keeps_attempts(ctx):
    db, cid = ctx
    repo.save_quiz_attempt(db, attempt(cid))
    init_db(db)
    init_db(db)
    assert repo.get_quiz_attempt(db, 'a1').total_count == 2
    with closing(get_connection(db)) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
