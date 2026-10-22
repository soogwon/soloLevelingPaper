"""근거 기반 단계별 퀴즈(M1-09, #54)의 검증·저장·조회를 실제 SQLite로 검증한다."""
from contextlib import closing

import pytest

from solo_leveling.application.evidence_qa.errors import ResourceNotFoundError
from solo_leveling.domain.learning_progress import LEARNING_STEPS
from solo_leveling.domain.models import Evidence
from solo_leveling.domain.quiz import (
    QUESTION_TYPES, Quiz, QuizQuestion, QuizValidationError, split_supported,
)
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


def mc(**kw):
    base = dict(question_type='multiple_choice', prompt='무엇이 첫째인가?', evidence_ids=('e1',),
                choices=('가', '나', '다'), correct_index=1)
    base.update(kw)
    return QuizQuestion(**base)


def short(**kw):
    base = dict(question_type='short_answer', prompt='둘째 문장은?', evidence_ids=['e2'],
                answer='Second', grading_criteria='Second라고 쓰면 정답')
    base.update(kw)
    return QuizQuestion(**base)


def questions():
    return [mc(), short()]


# ── 문항 검증 ───────────────────────────────────────────────
def test_question_types():
    assert set(QUESTION_TYPES) == {'multiple_choice', 'short_answer'}


def test_multiple_choice_answer_comes_from_choices():
    q = mc()
    assert q.answer == '나' and q.correct_index == 1


@pytest.mark.parametrize('kw', [
    {'evidence_ids': ()},                      # 근거 없음
    {'evidence_ids': ('',)},
    {'prompt': '  '},
    {'question_type': 'essay'},
    {'choices': ('가',), 'correct_index': 0},  # 선택지 부족
    {'choices': ('가', '나', '다', '라', '마', '바', '사'), 'correct_index': 0},
    {'choices': ('가', '가'), 'correct_index': 0},   # 중복
    {'choices': ('가', ' '), 'correct_index': 0},
    {'correct_index': None},                   # 정답 없음
    {'correct_index': 3},
    {'correct_index': -1},
    {'correct_index': True},
])
def test_invalid_multiple_choice(kw):
    with pytest.raises(QuizValidationError):
        mc(**kw)


@pytest.mark.parametrize('kw', [
    {'answer': ''},                            # 정답 없음
    {'grading_criteria': '  '},                # 채점 기준 없음
    {'choices': ('가', '나')},                 # 단답형은 선택지 없음
    {'correct_index': 0},
])
def test_invalid_short_answer(kw):
    with pytest.raises(QuizValidationError):
        short(**kw)


def test_split_supported_separates_unknown_evidence():
    ok, bad = split_supported(questions() + [short(evidence_ids=('zzz',))], ['e1', 'e2'])
    assert len(ok) == 2 and len(bad) == 1


# ── 저장·조회 ───────────────────────────────────────────────
def test_save_and_get_round_trip_keeps_order_and_answers(ctx):
    db, cid = ctx
    saved = repo.save_quiz(db, cid, 'preview', questions())
    got = repo.get_quiz(db, cid, 'preview')
    assert isinstance(got, Quiz) and got.quiz_id == saved.quiz_id and got.step == 'preview'
    first, second = got.questions
    assert (first.question_type, first.choices, first.correct_index, first.answer) == (
        'multiple_choice', ('가', '나', '다'), 1, '나')
    assert (second.question_type, second.choices, second.correct_index, second.answer,
            second.grading_criteria) == ('short_answer', (), None, 'Second', 'Second라고 쓰면 정답')
    assert first.evidence_ids == ('e1',) and second.evidence_ids == ('e2',)
    assert repo.list_quiz_steps(db, cid) == ['preview']


def test_every_stored_question_has_answer_and_checkable_evidence(ctx):
    db, cid = ctx
    quiz = repo.save_quiz(db, cid, 'review', questions())
    assert all(q.answer for q in quiz.questions)
    detail = repo.get_quiz_evidence(db, quiz).evidence
    assert [(d.quote_original, d.pdf_page) for d in detail] == [('First.', 1), ('Second.', 2)]


def test_missing_quiz_returns_none_and_steps_are_listed_in_learning_order(ctx):
    db, cid = ctx
    assert repo.get_quiz(db, cid, 'preview') is None
    repo.save_quiz(db, cid, 'review', [mc()])
    repo.save_quiz(db, cid, 'preview', [short()])
    assert repo.list_quiz_steps(db, cid) == [s for s in LEARNING_STEPS if s in ('preview', 'review')]


def test_saving_again_replaces_the_same_step_only(ctx):
    db, cid = ctx
    repo.save_quiz(db, cid, 'preview', questions())
    repo.save_quiz(db, cid, 'review', [short()])
    replaced = repo.save_quiz(db, cid, 'preview', [mc()])
    assert [q.question_type for q in repo.get_quiz(db, cid, 'preview').questions] == ['multiple_choice']
    assert repo.get_quiz(db, cid, 'preview').quiz_id == replaced.quiz_id
    assert len(repo.get_quiz(db, cid, 'review').questions) == 1
    with closing(get_connection(db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM quiz_questions').fetchone()[0] == 2


def test_rejected_save_keeps_existing_quiz(ctx):
    db, cid = ctx
    original = repo.save_quiz(db, cid, 'preview', questions())
    with pytest.raises(QuizValidationError):
        repo.save_quiz(db, cid, 'preview', [mc(), short(evidence_ids=('unknown',))])
    assert repo.get_quiz(db, cid, 'preview').quiz_id == original.quiz_id
    assert len(repo.get_quiz(db, cid, 'preview').questions) == 2


def test_unknown_evidence_step_empty_and_context_are_rejected(ctx):
    db, cid = ctx
    with pytest.raises(QuizValidationError):
        repo.save_quiz(db, cid, 'preview', [mc(evidence_ids=('nope',))])
    with pytest.raises(QuizValidationError):
        repo.save_quiz(db, cid, 'bogus', questions())
    with pytest.raises(QuizValidationError):
        repo.save_quiz(db, cid, 'preview', [])
    with pytest.raises(ResourceNotFoundError):
        repo.save_quiz(db, 'missing', 'preview', questions())
    assert repo.list_quiz_steps(db, cid) == []


def test_evidence_of_another_context_is_rejected(ctx):
    db, cid = ctx
    from solo_leveling.domain.models import LearningContext
    repo.create_learning_context(
        db, LearningContext('ctx2', 'v1', 'p1', 't1', known_concepts=['다른 개념'], embedding_set_id='idx'))
    with pytest.raises(QuizValidationError):
        repo.save_quiz(db, 'ctx2', 'preview', [mc(evidence_ids=('e1',))])
    assert repo.list_quiz_steps(db, 'ctx2') == []


def test_schema_is_repeatable_and_keeps_quiz_rows(ctx):
    db, cid = ctx
    repo.save_quiz(db, cid, 'preview', questions())
    init_db(db)
    init_db(db)
    assert len(repo.get_quiz(db, cid, 'preview').questions) == 2
    with closing(get_connection(db)) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
